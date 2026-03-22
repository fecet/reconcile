from pydantic import BaseModel, Field

from models.training import TrainingSpec
from reconcile import Unresolvable, dependency


class AdamWOptimizerSpec(BaseModel):
    lr: float = 1e-3
    decay_steps: int = Field(default=100, ge=0)

    @dependency
    def _lr_positive(self, _t: TrainingSpec) -> None:
        if self.lr <= 0:
            raise ValueError(f"lr={self.lr} must be positive")

    @dependency(decay_steps)
    def _(self, t: TrainingSpec) -> int:
        if t.num_steps < 100:
            raise Unresolvable
        return t.num_steps // 10


class WorkflowSpec(BaseModel):
    warmup_steps: int = 0
    training: TrainingSpec = Field()
    num_steps: int = Field(le=10000)
    lr: float = Field()

    @dependency(training)
    def _(self, training: TrainingSpec) -> TrainingSpec:
        return training

    @dependency(num_steps)
    def _(self) -> int:
        if self.training is None:
            raise Unresolvable
        if self.warmup_steps >= self.training.num_steps:
            raise ValueError(
                f"warmup ({self.warmup_steps}) >= total ({self.training.num_steps})"
            )
        return self.training.num_steps

    @dependency(lr)
    def _(self, o: AdamWOptimizerSpec) -> float:
        return o.lr
