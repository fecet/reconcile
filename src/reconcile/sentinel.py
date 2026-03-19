# Sentinel values — PEP 661 reference implementation
# Source: https://github.com/taleinat/python-stdlib-sentinels
# License: MIT (Copyright 2021-2022 Tal Einat)

import sys as _sys
from threading import Lock as _Lock

__all__ = ["Sentinel"]


class Sentinel:
    """Create a unique sentinel object.

    *name* should be the fully-qualified name of the variable to which the
    return value shall be assigned.

    *repr*, if supplied, will be used for the repr of the sentinel object.
    If not provided, "<name>" will be used (with any leading class names
    removed).

    *module_name*, if supplied, will be used instead of inspecting the call
    stack to find the name of the module from which
    """

    _name: str
    _repr: str
    _module_name: str

    def __new__(
        cls,
        name: str,
        repr: str | None = None,
        module_name: str | None = None,
    ):
        name = str(name)
        repr = str(repr) if repr else f"<{name.split('.')[-1]}>"
        if not module_name:
            parent_frame = _get_parent_frame()
            module_name = (
                parent_frame.f_globals.get("__name__", "__main__")
                if parent_frame is not None
                else __name__
            )

        # Include the class's module and fully qualified name in the
        # registry key to support sub-classing.
        registry_key = _sys.intern(
            f"{cls.__module__}-{cls.__qualname__}-{module_name}-{name}"
        )
        sentinel = _registry.get(registry_key, None)
        if sentinel is not None:
            return sentinel
        sentinel = super().__new__(cls)
        sentinel._name = name
        sentinel._repr = repr
        sentinel._module_name = module_name
        with _lock:
            return _registry.setdefault(registry_key, sentinel)

    def __repr__(self):
        return self._repr

    def __reduce__(self):
        return (
            self.__class__,
            (
                self._name,
                self._repr,
                self._module_name,
            ),
        )


_lock = _Lock()
_registry: dict[str, Sentinel] = {}


def _get_parent_frame():
    """Return the frame object for the caller's parent stack frame."""
    try:
        return _sys._getframe(2)
    except (AttributeError, ValueError):
        global _get_parent_frame

        def _get_parent_frame():
            try:
                raise Exception
            except Exception:
                try:
                    return _sys.exc_info()[2].tb_frame.f_back.f_back
                except Exception:
                    global _get_parent_frame

                    def _get_parent_frame():
                        return None

                    return _get_parent_frame()

        return _get_parent_frame()
