"""Mypy plugin: marks @dependency-targeted fields as init-optional.

PEP 681 @dataclass_transform makes Field() without default a required __init__
parameter. This plugin runs AFTER pydantic's hook synthesizes __init__, then
patches both the FuncDef and its CallableType to make dep fields optional.

Extends pydantic's mypy plugin — users configure a single entry:
    plugins = ["reconcile.mypy"]
"""

from typing import Callable

from mypy.nodes import (
    ARG_NAMED,
    ARG_NAMED_OPT,
    AssignmentStmt,
    CallExpr,
    Decorator,
    EllipsisExpr,
    FuncDef,
    NameExpr,
    OverloadedFuncDef,
)
from mypy.plugin import ClassDefContext, Plugin
from pydantic.mypy import PydanticPlugin


def _find_dep_field_names(ctx: ClassDefContext) -> set[str]:
    annotated_vars: set[str] = set()
    for stmt in ctx.cls.defs.body:
        if isinstance(stmt, AssignmentStmt) and stmt.type is not None:
            for lvalue in stmt.lvalues:
                if isinstance(lvalue, NameExpr):
                    annotated_vars.add(lvalue.name)

    dep_names: set[str] = set()
    for stmt in ctx.cls.defs.body:
        if not isinstance(stmt, Decorator):
            continue
        for dec in stmt.decorators:
            if not isinstance(dec, CallExpr) or not dec.args:
                continue
            callee = dec.callee
            if not isinstance(callee, NameExpr) or callee.name != "dependency":
                continue
            arg = dec.args[0]
            if isinstance(arg, NameExpr) and arg.name in annotated_vars:
                dep_names.add(arg.name)
    return dep_names


def _relax_init_args(func: FuncDef, dep_names: set[str]) -> None:
    for i, arg in enumerate(func.arguments):
        if arg.variable.name in dep_names and func.arg_kinds[i] == ARG_NAMED:
            func.arg_kinds[i] = ARG_NAMED_OPT
            if arg.initializer is None:
                arg.initializer = EllipsisExpr()
    # CallableType carries its own arg_kinds used for actual type checking
    from mypy.types import CallableType
    if isinstance(func.type, CallableType):
        for i, name in enumerate(func.type.arg_names):
            if name in dep_names and func.type.arg_kinds[i] == ARG_NAMED:
                func.type.arg_kinds[i] = ARG_NAMED_OPT


def _patch_init(ctx: ClassDefContext, dep_names: set[str]) -> None:
    init_sym = ctx.cls.info.names.get("__init__")
    if not init_sym or not init_sym.node:
        return
    node = init_sym.node
    if isinstance(node, FuncDef):
        _relax_init_args(node, dep_names)
    elif isinstance(node, OverloadedFuncDef):
        for item in node.items:
            inner = item.func if isinstance(item, Decorator) else item
            if isinstance(inner, FuncDef):
                _relax_init_args(inner, dep_names)


class ReconcilePlugin(PydanticPlugin):
    def get_base_class_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        pydantic_hook = super().get_base_class_hook(fullname)
        if pydantic_hook is not None:
            def combined(ctx: ClassDefContext) -> None:
                pydantic_hook(ctx)
                dep_names = _find_dep_field_names(ctx)
                if dep_names:
                    _patch_init(ctx, dep_names)
            return combined
        sym = self.lookup_fully_qualified(fullname)
        if sym and sym.node and hasattr(sym.node, "mro"):
            for base in sym.node.mro:
                if base.fullname == "pydantic.main.BaseModel":
                    def fallback(ctx: ClassDefContext) -> None:
                        dep_names = _find_dep_field_names(ctx)
                        if dep_names:
                            _patch_init(ctx, dep_names)
                    return fallback
        return None


def plugin(version: str) -> type[Plugin]:
    return ReconcilePlugin
