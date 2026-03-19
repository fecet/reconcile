"""Core implementation of reconcile — dependency resolution engine."""

import functools
import inspect
import typing
from collections import deque
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
    required: bool

    _pending: typing.ClassVar[dict[int, list["Dependency"]]] = {}

    def __init__(
        self, fn: Callable[..., Any], *, field: FieldInfo | None = None
    ) -> None:
        self.fn = fn
        self.required = False
        if field is not None:
            self._pending.setdefault(id(field), []).append(self)

    def __set_name__(self, owner: type, _name: str) -> None:
        ann = dict(owner.__annotations__)
        for fname, hint in ann.items():
            fi = owner.__dict__.get(fname)
            if not isinstance(fi, FieldInfo):
                continue
            deps = self._pending.pop(id(fi), [])
            if not deps:
                continue
            has_factory = fi.default_factory is not None
            required = fi.default is PydanticUndefined and not has_factory
            for dep in deps:
                dep.required = required
            if required:
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
    field = arg if isinstance(arg, FieldInfo) else None

    def decorator(fn: Callable[..., Any]) -> Any:
        return Dependency(fn, field=field)

    return decorator


class Unresolvable(Exception):
    pass


PENDING = Sentinel("PENDING")
UNRESOLVED = Sentinel("UNRESOLVED")
RESOLVING = Sentinel("RESOLVING")


@dataclass(eq=False, slots=True)
class FieldSlot:
    owner: BaseModel
    field_name: str
    providers: tuple[Dependency, ...]
    saved_default: Any
    required: bool
    result: Any = PENDING


class State(typing.NamedTuple):
    obj: BaseModel
    cls: type[BaseModel]
    slots: dict[str, FieldSlot]


@functools.cache
def _deps(
    cls: type[BaseModel],
) -> tuple[dict[str, tuple[Dependency, ...]], tuple[Dependency, ...]]:
    field_providers: dict[str, tuple[Dependency, ...]] = {}
    cross_validators: list[Dependency] = []
    seen: set[int] = set()

    for field_name, field_info in cls.model_fields.items():
        providers: list[Dependency] = []
        for metadata in field_info.metadata:
            if isinstance(metadata, Dependency) and id(metadata) not in seen:
                providers.append(metadata)
                seen.add(id(metadata))
        if providers:
            field_providers[field_name] = tuple(providers)

    for _, dep in inspect.getmembers(cls, lambda attr: isinstance(attr, Dependency)):
        if id(dep) not in seen:
            cross_validators.append(dep)
            seen.add(id(dep))

    return field_providers, tuple(cross_validators)


def _discover_participants(participants: tuple[Any, ...]) -> tuple[Any, ...]:
    all_objs = list(participants)
    seen_ids = {id(obj) for obj in all_objs}
    queue = deque(obj for obj in all_objs if isinstance(obj, BaseModel))
    while queue:
        obj = queue.popleft()
        for field_name in obj.model_fields_set:
            value = obj.__dict__.get(field_name)
            if isinstance(value, BaseModel) and id(value) not in seen_ids:
                seen_ids.add(id(value))
                all_objs.append(value)
                queue.append(value)
    return tuple(all_objs)


class Pool:
    _EXCLUDED: typing.ClassVar[set[type]] = {object, BaseModel}

    def __init__(self, participants: tuple[Any, ...]) -> None:
        self._data: dict[type, list[Any]] = {}
        for obj in participants:
            for cls in type(obj).__mro__:
                if cls in self._EXCLUDED:
                    continue
                self._data.setdefault(cls, []).append(obj)
        self._ns = {cls.__name__: cls for cls in self._data}

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
        kw: dict[str, Any] = {}
        if _AnnotationFormat is not None:
            kw["format"] = _AnnotationFormat.FORWARDREF
        hints = typing.get_type_hints(
            fn, globalns=self._ns, localns=getattr(fn, "__globals__", {}), **kw
        )
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


class ReconcileSession:
    def __init__(self, participants: tuple[Any, ...]) -> None:
        self.participants = participants
        all_objs = _discover_participants(participants)
        self.pool = Pool(all_objs)
        self.states: dict[int, State] = {}
        for obj in all_objs:
            if isinstance(obj, BaseModel):
                cls = type(obj)
                field_providers, _ = _deps(cls)
                slots = {
                    field_name: FieldSlot(
                        obj,
                        field_name,
                        providers,
                        obj.__dict__[field_name],
                        providers[0].required,
                    )
                    for field_name, providers in field_providers.items()
                    if field_name not in obj.model_fields_set
                }
                self.states[id(obj)] = State(obj, cls, slots)
        self.proxy_classes: dict[type, type] = {}
        self.resolution_stack: list[FieldSlot] = []

    def run(self) -> tuple[Any, ...]:
        try:
            for state in self.states.values():
                if not state.slots:
                    continue
                for slot in state.slots.values():
                    vars(state.obj).pop(slot.field_name)
                state.obj.__class__ = self._proxy_class_for(state.cls)

            for state in self.states.values():
                for slot in state.slots.values():
                    self._resolve_slot(slot)

            for state in self.states.values():
                for slot in state.slots.values():
                    if slot.result is not UNRESOLVED:
                        BaseModel.__setattr__(slot.owner, slot.field_name, slot.result)
                    else:
                        vars(slot.owner)[slot.field_name] = slot.saved_default

            for state in self.states.values():
                if type(state.obj) is not state.cls:
                    state.obj.__class__ = state.cls

            self.run_cross_validators()
            self.validate_fields()
        except BaseException:
            for state in self.states.values():
                if type(state.obj) is not state.cls:
                    state.obj.__class__ = state.cls
                for slot in state.slots.values():
                    if slot.field_name not in state.obj.__dict__:
                        vars(state.obj)[slot.field_name] = slot.saved_default
            raise
        return self.participants

    def run_cross_validators(self) -> None:
        for state in self.states.values():
            _, cross_validators = _deps(state.cls)
            for dep in cross_validators:
                self.pool.try_call(dep.fn.__get__(state.obj, state.cls))

    def validate_fields(self) -> None:
        for state in self.states.values():
            for slot in state.slots.values():
                if slot.required and slot.field_name not in state.obj.model_fields_set:
                    raise ValueError(
                        f"{state.cls.__name__}.{slot.field_name}: required but unresolved"
                    )
            for field_name in (
                set(state.slots) | state.obj.model_fields_set
            ) & state.cls.model_fields.keys():
                field_info = state.cls.model_fields[field_name]
                if field_info.metadata:
                    TypeAdapter(
                        typing.Annotated[field_info.annotation, *field_info.metadata]
                    ).validate_python(getattr(state.obj, field_name))

    def _cycle_error(self, slot: FieldSlot) -> ValueError:
        path = self.resolution_stack[self.resolution_stack.index(slot) :] + [slot]
        rendered = " -> ".join(
            f"{self.states[id(s.owner)].cls.__name__}.{s.field_name}" for s in path
        )
        return ValueError(f"Cycle detected: {rendered}")

    def _resolve_slot(self, slot: FieldSlot) -> Any:
        if slot.result is RESOLVING:
            raise self._cycle_error(slot)
        if slot.result is not PENDING:
            return slot.result

        state = self.states[id(slot.owner)]
        slot.result = RESOLVING
        self.resolution_stack.append(slot)
        try:
            result = UNRESOLVED
            for provider in slot.providers:
                result = self.pool.try_call(provider.fn.__get__(state.obj, state.cls))
                if result is not UNRESOLVED:
                    break
            slot.result = result
            return result
        except BaseException:
            slot.result = PENDING
            raise
        finally:
            self.resolution_stack.pop()

    def _proxy_getattr(self, obj: BaseModel, name: str) -> Any:
        state = self.states[id(obj)]
        slot = state.slots.get(name)
        if slot is None:
            return state.cls.__getattr__(state.obj, name)  # type: ignore[attr-defined]
        result = self._resolve_slot(slot)
        return slot.saved_default if result is UNRESOLVED else result

    def _proxy_class_for(self, cls: type[BaseModel]) -> type[BaseModel]:
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
