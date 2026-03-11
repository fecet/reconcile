from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from reconcile import dependency


class TrainingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_steps: int = 1000


class BaseLoss(BaseModel):
    weight: float = 1.0


class MSELoss(BaseLoss):
    reduction: str = "mean"


class MAELoss(BaseLoss):
    pass


class CrossEntropyLoss(BaseLoss):
    ignore_index: int = -100
    compile: bool = False

    def __call__(self, _logits: Any, _labels: Any) -> str:
        return f"ce_loss(ignore_index={self.ignore_index})"


class AdamWOptimizerSpec(BaseModel):
    lr: float = 1e-3
    betas: tuple[float, float] = (0.9, 0.999)
    weight_decay: float = 0.01

    @dependency
    def _lr_positive(self, _t: TrainingSpec) -> None:
        if self.lr <= 0:
            raise ValueError(f"lr={self.lr} must be positive")


class LinearWarmupSchedulerSpec(BaseModel):
    warmup_steps: int = 0
    lr_min: float = 0.0
    num_steps: int = Field()
    lr: float = Field()

    @dependency(num_steps)
    def _(self, t: TrainingSpec) -> int:
        if self.warmup_steps >= t.num_steps:
            raise ValueError(f"warmup ({self.warmup_steps}) >= total ({t.num_steps})")
        return t.num_steps

    @dependency(lr)
    def _(self, o: AdamWOptimizerSpec) -> float:
        return o.lr


class NeedsLoss(BaseModel):
    name: str = Field()

    @dependency(name)
    def _(self, loss: BaseLoss) -> str:
        return type(loss).__name__


class DataLoaderSpec(BaseModel):
    batch_size: int = Field(default=32, ge=1, le=10000)
    effective_lr: float = Field(default=0.001)
    tags: list[str] = Field(default_factory=list)

    @dependency(batch_size)
    def _(self, t: TrainingSpec) -> int:
        return t.num_steps

    @dependency(effective_lr)
    def _(self, o: AdamWOptimizerSpec) -> float:
        return o.lr

    @dependency(tags)
    def _(self, t: TrainingSpec) -> list[str]:
        return [f"steps={t.num_steps}"]


