from pydantic import BaseModel, ConfigDict, Field

from reconcile import dependency


class TrainingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    num_steps: int = 1000
    global_batch_size: int = Field(default=0)

    @dependency(global_batch_size)
    def _(self, p: "ParallelSpec") -> int:
        return p.local_batch_size * p.dp


class BaseLoss(BaseModel):
    weight: float = 1.0


class MSELoss(BaseLoss):
    reduction: str = "mean"


class MAELoss(BaseLoss):
    pass


class CompositeLoss(BaseLoss):
    mse: MSELoss
    mae: MAELoss


class NeedsLoss(BaseModel):
    name: str = Field()

    @dependency(name)
    def _(self, loss: BaseLoss) -> str:
        return type(loss).__name__
