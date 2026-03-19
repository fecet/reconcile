"""Core implementation of reconcile — dependency resolution engine."""

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from reconcile.sentinel import Sentinel

try:
    from annotationlib import Format as _AnnotationFormat
except ImportError:
    _AnnotationFormat = None  # type: ignore[assignment,misc]


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


UNRESOLVED = Sentinel("UNRESOLVED")
RESOLVING = Sentinel("RESOLVING")


@dataclass(eq=False, slots=True)
class FieldSlot:
    owner: BaseModel
    field_name: str
    providers: list[Dependency]
    saved_default: Any


class Pool:
    _EXCLUDED: typing.ClassVar[set[type]] = {object, BaseModel}

    def __init__(self, participants: tuple[Any, ...]) -> None:
        self._data: dict[type, list[Any]] = {}
        self._deps: dict[type, list[Dependency]] = {}
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

    def try_call(self, fn: Callable[..., Any]) -> Any:
        try:
            return self.call(fn)
        except Unresolvable:
            return UNRESOLVED

    def deps(self, cls: type[BaseModel]) -> list[Dependency]:
        if cls not in self._deps:
            # Metadata preserves declaration order from _pending
            result: list[Dependency] = []
            seen: set[int] = set()
            for fi in cls.model_fields.values():
                for m in fi.metadata:
                    if isinstance(m, Dependency) and id(m) not in seen:
                        result.append(m)
                        seen.add(id(m))
            for _, d in inspect.getmembers(cls, lambda a: isinstance(a, Dependency)):
                if id(d) not in seen:
                    result.append(d)
                    seen.add(id(d))
            self._deps[cls] = result
        return self._deps[cls]

class ReconcileSession:
    def __init__(self, participants: tuple[Any, ...]) -> None:
        self.participants = participants
        self.pool = Pool(participants)
        self.owners: dict[int, BaseModel] = {}
        self.owner_cls: dict[int, type[BaseModel]] = {}
        self.slots: dict[int, dict[str, FieldSlot]] = {}
        for obj in participants:
            if isinstance(obj, BaseModel):
                cls = type(obj)
                self.owners[id(obj)] = obj
                self.owner_cls[id(obj)] = cls
                self.slots[id(obj)] = self._build_slots(obj, cls)
        self.proxy_classes: dict[type, type] = {}
        self.resolved: dict[FieldSlot, Any] = {}

    def run(self) -> tuple[Any, ...]:
        try:
            self.promote_models()
            self.resolve_fields()
            self.commit_results()
            self.demote_models()
            self.run_cross_validators()
            self.validate_fields()
        except:
            self.demote_models()
            self.restore_defaults()
            raise
        return self.participants

    def _build_slots(self, obj: BaseModel, cls: type[BaseModel]) -> dict[str, FieldSlot]:
        by_field: dict[str, list[Dependency]] = {}
        for dep in self.pool.deps(cls):
            if dep.field_name is not None and dep.field_name not in obj.model_fields_set:
                by_field.setdefault(dep.field_name, []).append(dep)
        return {
            fname: FieldSlot(
                owner=obj,
                field_name=fname,
                providers=providers,
                saved_default=obj.__dict__[fname],
            )
            for fname, providers in by_field.items()
        }

    def promote_models(self) -> None:
        for obj_id, fields in self.slots.items():
            if not fields:
                continue
            obj = self.owners[obj_id]
            for slot in fields.values():
                vars(obj).pop(slot.field_name)
            obj.__class__ = self._proxy_class_for(self.owner_cls[obj_id])

    def resolve_fields(self) -> None:
        for fields in self.slots.values():
            for slot in fields.values():
                getattr(slot.owner, slot.field_name)

    def commit_results(self) -> None:
        for slot, result in self.resolved.items():
            if result is not UNRESOLVED:
                BaseModel.__setattr__(slot.owner, slot.field_name, result)
            else:
                vars(slot.owner)[slot.field_name] = slot.saved_default

    def run_cross_validators(self) -> None:
        for obj_id, cls in self.owner_cls.items():
            obj = self.owners[obj_id]
            for dep in self.pool.deps(cls):
                if dep.field_name is not None:
                    continue
                self.pool.try_call(dep.fn.__get__(obj, cls))

    def validate_fields(self) -> None:
        for obj_id, cls in self.owner_cls.items():
            obj = self.owners[obj_id]
            fields = self.slots[obj_id]
            for dep in self.pool.deps(cls):
                if not dep.required or dep.field_name is None:
                    continue
                if dep.field_name not in obj.model_fields_set:
                    raise ValueError(
                        f"{cls.__name__}.{dep.field_name}: required but unresolved"
                    )
            for field_name in set(fields) | obj.model_fields_set:
                fi = cls.model_fields[field_name]
                if fi.metadata:
                    ta: TypeAdapter[Any] = TypeAdapter(typing.Annotated[fi.annotation, *fi.metadata])
                    ta.validate_python(getattr(obj, field_name))

    def demote_models(self) -> None:
        for obj_id, cls in self.owner_cls.items():
            obj = self.owners[obj_id]
            if type(obj) is not cls:
                obj.__class__ = cls

    def restore_defaults(self) -> None:
        for fields in self.slots.values():
            for slot in fields.values():
                if slot.field_name not in slot.owner.__dict__:
                    vars(slot.owner)[slot.field_name] = slot.saved_default

    def _cycle_error(self, slot: FieldSlot) -> ValueError:
        stack = [s for s, r in self.resolved.items() if r is RESOLVING]
        path = stack[stack.index(slot):] + [slot]
        rendered = " -> ".join(
            f"{self.owner_cls[id(s.owner)].__name__}.{s.field_name}"
            for s in path
        )
        return ValueError(f"Cycle detected: {rendered}")

    def _proxy_getattr(self, obj: BaseModel, name: str) -> Any:
        oid = id(obj)
        cls = self.owner_cls[oid]
        slot = self.slots[oid].get(name)
        if slot is None:
            return cls.__getattr__(obj, name)  # type: ignore[attr-defined]
        if slot not in self.resolved:
            self.resolved[slot] = RESOLVING
            result = UNRESOLVED
            for provider in slot.providers:
                result = self.pool.try_call(provider.fn.__get__(obj, cls))
                if result is not UNRESOLVED:
                    break
            self.resolved[slot] = result
        result = self.resolved[slot]
        if result is RESOLVING:
            raise self._cycle_error(slot)
        return slot.saved_default if result is UNRESOLVED else result

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
