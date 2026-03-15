"""Mypy plugin: marks @dependency-targeted fields as init-optional.

PEP 681 @dataclass_transform makes Field() without default a required __init__
parameter. This plugin injects a synthetic ``default`` keyword into the Field()
call AST for dep fields, so the transform treats them as optional.

Usage in pyproject.toml:
    plugins = ["pydantic.mypy", "reconcile.mypy"]
"""

from typing import Callable

from mypy.nodes import (
    ARG_NAMED,
    AssignmentStmt,
    CallExpr,
    Decorator,
    EllipsisExpr,
    NameExpr,
)
from mypy.plugin import ClassDefContext, Plugin


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


def _inject_defaults_into_field_calls(ctx: ClassDefContext) -> None:
    dep_names = _find_dep_field_names(ctx)
    if not dep_names:
        return

    for stmt in ctx.cls.defs.body:
        if not isinstance(stmt, AssignmentStmt) or stmt.type is None:
            continue
        for lvalue in stmt.lvalues:
            if not isinstance(lvalue, NameExpr) or lvalue.name not in dep_names:
                continue
            rhs = stmt.rvalue
            if not isinstance(rhs, CallExpr):
                continue
            has_default = any(
                n in ("default", "default_factory") for n in rhs.arg_names
            )
            if not has_default:
                rhs.arg_names.append("default")
                rhs.args.append(EllipsisExpr())
                rhs.arg_kinds.append(ARG_NAMED)


class ReconcilePlugin(Plugin):
    def get_base_class_hook(self, fullname: str) -> Callable[[ClassDefContext], None] | None:
        sym = self.lookup_fully_qualified(fullname)
        if sym and sym.node and hasattr(sym.node, "mro"):
            for base in sym.node.mro:
                if base.fullname == "pydantic.main.BaseModel":
                    return _inject_defaults_into_field_calls
        return None


def plugin(version: str) -> type[Plugin]:
    return ReconcilePlugin
