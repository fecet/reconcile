"""reconcile — declarative cross-object field resolution for Pydantic models.

``dependency`` declares cross-object field derivations and validators.
``reconcile`` resolves all dependencies to a consistent state.
"""

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

try:
    from annotationlib import Format as _AnnotationFormat
except ImportError:
    _AnnotationFormat = None


# Inherit property so Pydantic treats us as a descriptor rather than
# replacing the attribute with ModelPrivateAttr during model creation.
class Dependency(property):
    fn: Callable[..., Any]
    field_name: str | None
    required: bool
    target: Any

    _pending: typing.ClassVar[dict[int, list["Dependency"]]] = {}

    def __init__(self, fn: Callable[..., Any], *, target: Any = None) -> None:
        self.fn = fn
        self.field_name = None
        self.required = False
        self.target = target
        if isinstance(target, FieldInfo):
            self._pending.setdefault(id(target), []).append(self)

    def __set_name__(self, owner: type, name: str) -> None:
        ann = dict(owner.__annotations__)
        for fname, hint in ann.items():
            fi = owner.__dict__.get(fname)
            if not isinstance(fi, FieldInfo):
                continue
            deps = self._pending.pop(id(fi), [])
            if not deps:
                continue
            if len(deps) > 1:
                raise TypeError(f"{owner.__name__}.{fname}: multiple providers")
            has_factory = fi.default_factory is not None
            for dep in deps:
                dep.field_name = fname
                dep.required = fi.default is PydanticUndefined and not has_factory
            if not has_factory and deps[0].required:
                fi.default = None
            ann[fname] = typing.Annotated[hint, fi, *deps]
            if has_factory:
                delattr(owner, fname)
            else:
                setattr(owner, fname, fi.default)
        owner.__annotations__ = ann


def dependency(arg: Any = None, /) -> Any:
    if callable(arg) and not isinstance(arg, FieldInfo):
        return Dependency(arg)
    target = arg

    def decorator(fn: Callable[..., Any]) -> Any:
        return Dependency(fn, target=target)

    return decorator


class Unresolvable(Exception):
    pass


@dataclass(slots=True)
class ReconcileModel:
    owner: BaseModel
    owner_cls: type[BaseModel]
    fields: dict[str, "ReconcileField"]

    def promote(self, proxy_cls: type) -> None:
        for field in self.fields.values():
            self.owner.__dict__.pop(field.field_name)
        self.owner.__class__ = proxy_cls

    def resolve_fields(self) -> None:
        for field in self.fields.values():
            getattr(self.owner, field.field_name)

    def demote(self) -> None:
        if type(self.owner) is not self.owner_cls:
            self.owner.__class__ = self.owner_cls

    def restore_defaults(self) -> None:
        for field in self.fields.values():
            if field.field_name not in self.owner.__dict__:
                field.restore_default()


@dataclass(eq=False, slots=True)
class ReconcileField:
    model: ReconcileModel
    field_name: str
    provider: Dependency
    saved_default: Any

    @property
    def label(self) -> str:
        return f"{self.model.owner_cls.__name__}.{self.field_name}"

    def restore_default(self) -> None:
        self.model.owner.__dict__[self.field_name] = self.saved_default

    def apply_resolution(self, result: Any | None) -> Any:
        if result is None:
            self.restore_default()
        else:
            BaseModel.__setattr__(self.model.owner, self.field_name, result)
        return self.model.owner.__dict__[self.field_name]


class Pool:
    _EXCLUDED: typing.ClassVar[set[type]] = {object, BaseModel}

    def __init__(self, participants: tuple[Any, ...]) -> None:
        self._data: dict[type, list[Any]] = {}
        self._deps: dict[type, list[Dependency]] = {}
        self._field_providers: dict[type, dict[str, Dependency]] = {}
        for obj in participants:
            for cls in type(obj).__mro__:
                if cls in self._EXCLUDED:
                    continue
                self._data.setdefault(cls, []).append(obj)

    def resolve(self, requested: type) -> Any:
        candidates = self._data.get(requested, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(type(c).__name__ for c in candidates)
            raise TypeError(
                f"Ambiguous: multiple candidates for {requested.__name__}: {names}"
            )
        raise KeyError(requested)

    def call(self, fn: Callable[..., Any]) -> Any:
        ns = {cls.__name__: cls for cls in self._data}
        ns.update(getattr(fn, "__globals__", {}))
        kw: dict[str, Any] = {}
        if _AnnotationFormat is not None:
            kw["format"] = _AnnotationFormat.FORWARDREF
        hints = typing.get_type_hints(fn, globalns=ns, **kw)
        hints.pop("return", None)
        try:
            kwargs = {p: self.resolve(t) for p, t in hints.items()}
        except KeyError:
            raise Unresolvable
        return fn(**kwargs)

    def try_call(self, fn: Callable[..., Any]) -> Any | None:
        try:
            return self.call(fn)
        except Unresolvable:
            return None

    def deps(self, cls: type) -> list[Dependency]:
        if cls not in self._deps:
            result = [
                d
                for _, d in inspect.getmembers(cls, lambda a: isinstance(a, Dependency))
            ]
            seen = {id(d) for d in result}
            for fi in cls.model_fields.values():
                for m in fi.metadata:
                    if isinstance(m, Dependency) and id(m) not in seen:
                        result.append(m)
                        seen.add(id(m))
            self._deps[cls] = result
        return self._deps[cls]

    def field_providers(self, cls: type) -> dict[str, Dependency]:
        if cls not in self._field_providers:
            result: dict[str, Dependency] = {}
            for dep in self.deps(cls):
                if dep.field_name is None:
                    continue
                result[dep.field_name] = dep
            self._field_providers[cls] = result
        return self._field_providers[cls]


class ReconcileSession:
    def __init__(self, participants: tuple[Any, ...]) -> None:
        self.participants = participants
        self.pool = Pool(participants)
        self.models = [
            self._build_reconcile_model(obj)
            for obj in participants
            if isinstance(obj, BaseModel)
        ]
        self.proxy_classes: dict[type, type] = {}
        self.models_by_object_identity = {
            id(model.owner): model for model in self.models
        }
        self.resolution_stack: list[ReconcileField] = []
        self.resolving_fields: set[ReconcileField] = set()

    def run(self) -> tuple[Any, ...]:
        try:
            self.promote_models()
            self.resolve_fields()
            self.demote_models()
            self.run_cross_validators()
            self.validate_fields()
        finally:
            self.demote_models()
            self.restore_defaults()
        return self.participants

    def _build_reconcile_model(self, obj: BaseModel) -> ReconcileModel:
        owner_cls = type(obj)
        model = ReconcileModel(owner=obj, owner_cls=owner_cls, fields={})
        model.fields = {
            field_name: ReconcileField(
                model=model,
                field_name=field_name,
                provider=provider,
                saved_default=obj.__dict__[field_name],
            )
            for field_name, provider in self.pool.field_providers(owner_cls).items()
            if field_name not in obj.model_fields_set
        }
        return model

    def promote_models(self) -> None:
        for model in self.models:
            if model.fields:
                model.promote(self._proxy_class_for(model.owner_cls))

    def resolve_fields(self) -> None:
        for model in self.models:
            model.resolve_fields()

    def run_cross_validators(self) -> None:
        for model in self.models:
            for meta in self.pool.deps(model.owner_cls):
                if meta.field_name is not None:
                    continue
                self.pool.try_call(meta.fn.__get__(model.owner, model.owner_cls))

    def validate_fields(self) -> None:
        for model in self.models:
            for meta in self.pool.deps(model.owner_cls):
                if not meta.required or meta.field_name is None:
                    continue
                if meta.field_name not in model.owner.model_fields_set:
                    raise ValueError(
                        f"{model.owner_cls.__name__}.{meta.field_name}: required but unresolved"
                    )
            for field_name in model.owner.model_fields_set:
                fi = model.owner_cls.model_fields[field_name]
                if fi.metadata:
                    ta = TypeAdapter(typing.Annotated[fi.annotation, *fi.metadata])
                    ta.validate_python(getattr(model.owner, field_name))

    def demote_models(self) -> None:
        for model in self.models:
            model.demote()

    def restore_defaults(self) -> None:
        for model in self.models:
            model.restore_defaults()

    def _cycle_error(self, field: ReconcileField) -> ValueError:
        start = self.resolution_stack.index(field)
        path = self.resolution_stack[start:] + [field]
        rendered = " -> ".join(item.label for item in path)
        return ValueError(f"Cycle detected: {rendered}")

    def _proxy_getattr(self, obj: BaseModel, name: str) -> Any:
        model = self.models_by_object_identity[id(obj)]
        field = model.fields.get(name)
        if field is None:
            return model.owner_cls.__getattr__(obj, name)
        if field in self.resolving_fields:
            raise self._cycle_error(field)
        self.resolution_stack.append(field)
        self.resolving_fields.add(field)
        try:
            result = self.pool.try_call(
                field.provider.fn.__get__(obj, model.owner_cls)
            )
            return field.apply_resolution(result)
        finally:
            self.resolving_fields.remove(field)
            self.resolution_stack.pop()

    def _proxy_class_for(self, cls: type) -> type:
        if cls not in self.proxy_classes:

            def __getattr__(obj: BaseModel, name: str) -> Any:
                return self._proxy_getattr(obj, name)

            self.proxy_classes[cls] = type(
                cls.__name__,
                (cls,),
                {
                    "__getattr__": __getattr__,
                    "__module__": cls.__module__,
                    "__qualname__": cls.__qualname__,
                },
            )
        return self.proxy_classes[cls]


def reconcile[*Ts](*participants: *Ts) -> tuple[*Ts]:
    return ReconcileSession(participants).run()
