from pydantic import BaseModel, ConfigDict, Field

from reconcile import dependency


class MutualA(BaseModel):
    model_config = ConfigDict(validate_default=True, validate_assignment=True)
    value: int = Field()

    @dependency(value)
    def _(self, b: "MutualB") -> int:
        return b.value + 1


class MutualB(BaseModel):
    model_config = ConfigDict(validate_default=True, validate_assignment=True)
    value: int = Field()

    @dependency(value)
    def _(self, a: MutualA) -> int:
        return a.value + 1


class NodeX(BaseModel):
    model_config = ConfigDict(validate_default=True, validate_assignment=True)
    value: int = Field(default=0)

    @dependency(value)
    def _(self, y: "NodeY") -> int:
        return y.value + 1


class NodeY(BaseModel):
    model_config = ConfigDict(validate_default=True, validate_assignment=True)
    value: int = Field(default=0)

    @dependency(value)
    def _(self, x: NodeX) -> int:
        return x.value + 1


class Ring1(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    value: int = Field(default=0)

    @dependency(value)
    def _(self, r3: "Ring3") -> int:
        return r3.value + 1


class Ring2(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    value: int = Field(default=0)

    @dependency(value)
    def _(self, r1: Ring1) -> int:
        return r1.value + 1


class Ring3(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    value: int = Field(default=0)

    @dependency(value)
    def _(self, r2: Ring2) -> int:
        return r2.value + 1
