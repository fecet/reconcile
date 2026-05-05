"""Build a typed schema instance from an env-var-selected config submodule.

A peer primitive to :func:`reconcile.reconcile`; the two compose in any order.
The schema can be a ``NamedTuple``, ``@dataclass``, or any class whose
``__init__`` accepts the schema's fields as keyword arguments.
"""

import importlib
import os
from typing import TypeVar

T = TypeVar("T")


def realize(
    schema: type[T],
    *,
    env: str,
    default: str = "default",
    package: str = "configs",
    into: dict[str, object] | None = None,
) -> T:
    """Build an instance of ``schema`` from ``<package>.<env or default>``.

    If ``into`` is given, the same field values used to construct the instance
    are also written into it (typically ``globals()``).
    """
    name = os.environ.get(env, default)
    source = importlib.import_module(f".{name}", package=package)

    kwargs = {
        field: getattr(source, field)
        for field in schema.__annotations__
        if hasattr(source, field)
    }
    if into is not None:
        into.update(kwargs)
    return schema(**kwargs)
