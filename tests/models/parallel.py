import sys

from pydantic import BaseModel, Field

from reconcile import dependency


class MoESpec(BaseModel):
    num_experts: int = 8


class ParallelSpec(BaseModel):
    dp: int = 1
    local_batch_size: int = Field(default=0)
    moe: MoESpec = MoESpec()

    @dependency(local_batch_size)
    def _(self, t: "TrainingSpec") -> int:
        return t.global_batch_size // self.dp

    @dependency
    def _validate_expert_count(self, t: "TrainingSpec") -> None:
        if t.num_steps % self.moe.num_experts != 0:
            raise ValueError(
                f"num_steps ({t.num_steps}) must be divisible by "
                f"num_experts ({self.moe.num_experts})"
            )


class ScaleSpec(BaseModel):
    factor: float = 1.0
    scaled_steps: int = Field(default=0)

    @dependency(scaled_steps)
    def _(self, t: "TrainingSpec", p: ParallelSpec) -> int:
        return int(t.num_steps * self.factor * p.moe.num_experts)


if sys.version_info >= (3, 14):

    class BareRefScale(BaseModel):
        steps: int = Field(default=0)

        @dependency(steps)
        def _(self, t: TrainingSpec) -> int:
            return t.num_steps * 2
