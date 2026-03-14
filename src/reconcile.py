"""reconcile — declarative cross-object field resolution for Pydantic models.

``dependency`` declares cross-object field derivations and validators.
``reconcile`` resolves all dependencies to a consistent state.
"""

import inspect
import typing
from collections.abc import Callable
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

    def deps(self, cls: type) -> list[Dependency]:
        if cls not in self._deps:
            result = [d for _, d in inspect.getmembers(cls, lambda a: isinstance(a, Dependency))]
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
                existing = result.get(dep.field_name)
                if existing is not None and existing is not dep:
                    raise TypeError(f"{cls.__name__}.{dep.field_name}: multiple providers")
                result[dep.field_name] = dep
            self._field_providers[cls] = result
        return self._field_providers[cls]


def reconcile[*Ts](*participants: *Ts) -> tuple[*Ts]:
    pool = Pool(participants)
    models = [obj for obj in participants if isinstance(obj, BaseModel)]
    original_classes = {id(obj): type(obj) for obj in models}
    field_providers = {
        cls: pool.field_providers(cls) for cls in {type(obj) for obj in models}
    }
    proxy_classes: dict[type, type] = {}
    model_by_id = {id(obj): obj for obj in models}
    saved_defaults: dict[tuple[int, str], Any] = {}
    resolution_stack: list[tuple[int, str]] = []
    resolving_fields: set[tuple[int, str]] = set()

    def field_label(key: tuple[int, str]) -> str:
        obj_id, field_name = key
        return f"{original_classes[obj_id].__name__}.{field_name}"

    def restore_default(obj: BaseModel, field_name: str) -> None:
        obj.__dict__[field_name] = saved_defaults[(id(obj), field_name)]

    def resolve_field(fn: Callable[..., Any]) -> Any | None:
        try:
            return pool.call(fn)
        except Unresolvable:
            return None

    def apply_resolution(obj: BaseModel, field_name: str, result: Any | None) -> Any:
        if result is None:
            restore_default(obj, field_name)
        else:
            BaseModel.__setattr__(obj, field_name, result)
        return obj.__dict__[field_name]

    def cycle_error(key: tuple[int, str]) -> ValueError:
        start = resolution_stack.index(key)
        path = resolution_stack[start:] + [key]
        rendered = " -> ".join(field_label(item) for item in path)
        return ValueError(f"Cycle detected: {rendered}")

    def proxy_class_for(cls: type) -> type:
        if cls not in proxy_classes:

            def __getattr__(self: BaseModel, name: str) -> Any:
                meta = field_providers[cls].get(name)
                if meta is None:
                    return cls.__getattr__(self, name)
                key = (id(self), name)
                if key in resolving_fields:
                    raise cycle_error(key)
                resolution_stack.append(key)
                resolving_fields.add(key)
                try:
                    result = resolve_field(meta.fn.__get__(self, cls))
                    return apply_resolution(self, name, result)
                finally:
                    resolving_fields.remove(key)
                    resolution_stack.pop()

            proxy_classes[cls] = type(
                cls.__name__,
                (cls,),
                {
                    "__getattr__": __getattr__,
                    "__module__": cls.__module__,
                    "__qualname__": cls.__qualname__,
                },
            )
        return proxy_classes[cls]

    def demote_models() -> None:
        for obj in models:
            original_cls = original_classes[id(obj)]
            if type(obj) is not original_cls:
                obj.__class__ = original_cls

    try:
        for obj in models:
            original_cls = original_classes[id(obj)]
            for field_name in field_providers[original_cls]:
                if field_name in obj.model_fields_set:
                    continue
                saved_defaults[(id(obj), field_name)] = obj.__dict__.pop(field_name)
            obj.__class__ = proxy_class_for(original_cls)

        for obj in models:
            original_cls = original_classes[id(obj)]
            for field_name in field_providers[original_cls]:
                if field_name in obj.model_fields_set:
                    continue
                getattr(obj, field_name)

        demote_models()

        # Phase 2: Cross-validate — run dependency validators across objects
        for obj in models:
            cls = type(obj)
            for meta in pool.deps(cls):
                if meta.field_name is not None:
                    continue
                try:
                    pool.call(meta.fn.__get__(obj, cls))
                except Unresolvable:
                    continue

        # Phase 3: Field validate — check completeness and Field constraints
        for obj in models:
            cls = type(obj)
            for meta in pool.deps(cls):
                if not meta.required or meta.field_name is None:
                    continue
                if meta.field_name not in obj.model_fields_set:
                    raise ValueError(
                        f"{cls.__name__}.{meta.field_name}: required but unresolved"
                    )
            for field_name in obj.model_fields_set:
                fi = cls.model_fields[field_name]
                if fi.metadata:
                    ta = TypeAdapter(typing.Annotated[fi.annotation, *fi.metadata])
                    ta.validate_python(getattr(obj, field_name))
    finally:
        demote_models()
        for (obj_id, field_name), _ in saved_defaults.items():
            obj = model_by_id[obj_id]
            if field_name not in obj.__dict__:
                restore_default(obj, field_name)

    return participants
