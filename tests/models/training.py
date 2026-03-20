from pydantic import BaseModel, ConfigDict, Field

from reconcile import Unresolvable, dependency


class TrainingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_steps: int = 1000


class BaseLoss(BaseModel):
    weight: float = 1.0


class MSELoss(BaseLoss):
    reduction: str = "mean"


class MAELoss(BaseLoss):
    pass


class CompositeLoss(BaseLoss):
    mse: MSELoss
    mae: MAELoss


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
    def _(self, training: "TrainingSpec") -> TrainingSpec:
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


class MoESpec(BaseModel):
    num_experts: int = 8


class ParallelSpec(BaseModel):
    dp: int = 1
    moe: MoESpec = MoESpec()

    @dependency
    def _validate_expert_count(self, t: TrainingSpec) -> None:
        if t.num_steps % self.moe.num_experts != 0:
            raise ValueError(
                f"num_steps ({t.num_steps}) must be divisible by "
                f"num_experts ({self.moe.num_experts})"
            )


class ScaleSpec(BaseModel):
    factor: float = 1.0
    scaled_steps: int = Field(default=0)

    @dependency(scaled_steps)
    def _(self, t: TrainingSpec, p: ParallelSpec) -> int:
        return int(t.num_steps * self.factor * p.moe.num_experts)


class NeedsLoss(BaseModel):
    name: str = Field()

    @dependency(name)
    def _(self, loss: BaseLoss) -> str:
        return type(loss).__name__
