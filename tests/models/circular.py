from pydantic import BaseModel

from reconcile import deferred, dependency


class MutualA(BaseModel):
    value: int = deferred()

    @dependency(value)
    def _(self, b: "MutualB") -> int | None:
        if b.value is None:
            return None
        return b.value + 1


class MutualB(BaseModel):
    value: int = deferred()

    @dependency(value)
    def _(self, a: MutualA) -> int | None:
        if a.value is None:
            return None
        return a.value + 1


class NodeX(BaseModel):
    value: int = deferred(default=0)

    @dependency(value)
    def _(self, y: "NodeY") -> int:
        return y.value + 1


class NodeY(BaseModel):
    value: int = deferred(default=0)

    @dependency(value)
    def _(self, x: NodeX) -> int:
        return x.value + 1


class Ring1(BaseModel):
    value: int = deferred(default=0)

    @dependency(value)
    def _(self, r3: "Ring3") -> int:
        return r3.value + 1


class Ring2(BaseModel):
    value: int = deferred(default=0)

    @dependency(value)
    def _(self, r1: Ring1) -> int:
        return r1.value + 1


class Ring3(BaseModel):
    value: int = deferred(default=0)

    @dependency(value)
    def _(self, r2: Ring2) -> int:
        return r2.value + 1
