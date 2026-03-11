from typing import Any

import pytest
from pydantic import BaseModel, Field

from models.training import (
    AdamWOptimizerSpec,
    CrossEntropyLoss,
    DataLoaderSpec,
    LinearWarmupSchedulerSpec,
    MAELoss,
    MSELoss,
    NeedsLoss,
    TrainingSpec,
)

from reconcile import dependency, reconcile


def assert_reconciled(*instances: Any, expect: dict[int, dict[str, Any]]) -> tuple[Any, ...]:
    results = reconcile(*instances)
    for idx, fields in expect.items():
        for field, value in fields.items():
            assert getattr(results[idx], field) == value, (
                f"results[{idx}].{field}: expected {value!r}, got {getattr(results[idx], field)!r}"
            )
    return results


class TestResolution:
    def test_cross_object(self):
        assert_reconciled(
            LinearWarmupSchedulerSpec(warmup_steps=100),
            TrainingSpec(num_steps=2000),
            AdamWOptimizerSpec(lr=1e-3),
            expect={0: {"num_steps": 2000, "lr": 1e-3}},
        )

    def test_manual_override(self):
        assert_reconciled(
            LinearWarmupSchedulerSpec(warmup_steps=100, num_steps=999, lr=1e-2),
            TrainingSpec(num_steps=2000),
            AdamWOptimizerSpec(lr=5e-4),
            expect={0: {"num_steps": 999, "lr": 1e-2}},
        )

    def test_multi_participant(self):
        loss, *_ = assert_reconciled(
            CrossEntropyLoss(),
            AdamWOptimizerSpec(lr=3e-4),
            LinearWarmupSchedulerSpec(warmup_steps=200),
            TrainingSpec(num_steps=5000),
            expect={
                1: {"lr": 3e-4},
                2: {"num_steps": 5000, "lr": 3e-4},
            },
        )
        assert loss("logits", "labels") == "ce_loss(ignore_index=-100)"

    def test_skip_when_dependency_absent(self):
        assert_reconciled(
            LinearWarmupSchedulerSpec(warmup_steps=100, num_steps=500, lr=0.01),
            expect={0: {"num_steps": 500, "lr": 0.01}},
        )

    def test_model_fields_and_dump(self):
        assert "num_steps" in LinearWarmupSchedulerSpec.model_fields
        assert "lr" in LinearWarmupSchedulerSpec.model_fields
        assert LinearWarmupSchedulerSpec().model_dump() == {
            "warmup_steps": 0,
            "lr_min": 0.0,
            "num_steps": None,
            "lr": None,
        }
        assert LinearWarmupSchedulerSpec(num_steps=42, lr=0.5).num_steps == 42
        assert LinearWarmupSchedulerSpec(num_steps=42, lr=0.5).lr == 0.5


class TestErrors:
    def test_duplicate_type(self):
        with pytest.raises(TypeError, match="Ambiguous"):
            reconcile(TrainingSpec(), TrainingSpec(), AdamWOptimizerSpec())

    def test_required_unresolved(self):
        with pytest.raises(ValueError, match="required but unresolved"):
            reconcile(LinearWarmupSchedulerSpec(warmup_steps=100))

    def test_derivation_validation_error(self):
        with pytest.raises(ValueError, match=r"warmup \(5000\) >= total \(2000\)"):
            reconcile(
                LinearWarmupSchedulerSpec(warmup_steps=5000),
                TrainingSpec(num_steps=2000),
                AdamWOptimizerSpec(),
            )

    def test_fail_fast(self):
        with pytest.raises(ValueError):
            reconcile(
                AdamWOptimizerSpec(lr=0),
                LinearWarmupSchedulerSpec(warmup_steps=5000),
                TrainingSpec(num_steps=2000),
            )

    def test_subclass_ambiguity(self):
        with pytest.raises(TypeError, match="Ambiguous"):
            reconcile(NeedsLoss(), MSELoss(), MAELoss())

        a, mse, mae = NeedsLoss(), MSELoss(), MAELoss()
        reconcile(a, mse)
        assert a.name == "MSELoss"
        reconcile(a, mae)
        assert a.name == "MSELoss"  # already set, not overwritten


class TestFeatures:
    def test_field_constraints_validated(self):
        with pytest.raises(ValueError, match="less than or equal to 10000"):
            reconcile(DataLoaderSpec(), TrainingSpec(num_steps=99999))

        assert_reconciled(
            DataLoaderSpec(), TrainingSpec(num_steps=50), AdamWOptimizerSpec(),
            expect={0: {"batch_size": 50}},
        )

    def test_subclass_resolution(self):
        loss, _ = assert_reconciled(MSELoss(), TrainingSpec(), expect={0: {"weight": 1.0}})
        assert isinstance(loss, MSELoss)

    def test_string_annotation_resolved_from_pool(self):
        class Alpha(BaseModel):
            value: int = 10

        class Beta(BaseModel):
            derived: int = Field()

            @dependency(derived)
            def _(self, a: "Alpha") -> int:
                return a.value * 2

        assert_reconciled(Beta(), Alpha(value=7), expect={0: {"derived": 14}})

    def test_field_default_as_fallback(self):
        assert_reconciled(
            DataLoaderSpec(),
            TrainingSpec(num_steps=5000),
            AdamWOptimizerSpec(lr=0.01),
            expect={0: {"batch_size": 5000, "effective_lr": 0.01, "tags": ["steps=5000"]}},
        )

        assert_reconciled(
            DataLoaderSpec(),
            expect={0: {"batch_size": 32, "effective_lr": 0.001, "tags": []}},
        )

        assert_reconciled(
            DataLoaderSpec(tags=["manual"]),
            TrainingSpec(num_steps=5000),
            AdamWOptimizerSpec(lr=0.01),
            expect={0: {"tags": ["manual"]}},
        )

    def test_multiple_deps_on_factory_field(self):
        class Multi(BaseModel):
            items: list[str] = Field(default_factory=list)

            @dependency(items)
            def _a(self, t: TrainingSpec) -> list[str]:
                return [f"steps={t.num_steps}"]

            @dependency(items)
            def _b(self, o: AdamWOptimizerSpec) -> list[str]:
                return [f"lr={o.lr}"]

        spec, _, _ = reconcile(
            Multi(), TrainingSpec(num_steps=5000), AdamWOptimizerSpec(lr=0.01)
        )
        assert spec.items in [["steps=5000"], ["lr=0.01"]]

        assert_reconciled(Multi(), expect={0: {"items": []}})


class TestCircular:
    def test_mutual_required_both_unset(self):
        from models.circular import MutualA, MutualB

        with pytest.raises(ValueError, match="required but unresolved"):
            reconcile(MutualA(), MutualB())

    def test_mutual_required_one_seeded(self):
        from models.circular import MutualA, MutualB

        assert_reconciled(
            MutualA(value=5), MutualB(),
            expect={0: {"value": 5}, 1: {"value": 6}},
        )
        assert_reconciled(
            MutualA(), MutualB(value=10),
            expect={0: {"value": 11}, 1: {"value": 10}},
        )

    def test_mutual_with_defaults(self):
        from models.circular import NodeX, NodeY

        assert_reconciled(
            NodeX(), NodeY(),
            expect={0: {"value": 1}, 1: {"value": 2}},
        )

    def test_ring_no_seed(self):
        from models.circular import Ring1, Ring2, Ring3

        assert_reconciled(
            Ring1(), Ring2(), Ring3(),
            expect={0: {"value": 1}, 1: {"value": 2}, 2: {"value": 3}},
        )

    def test_ring_one_seeded(self):
        from models.circular import Ring1, Ring2, Ring3

        assert_reconciled(
            Ring1(value=10), Ring2(), Ring3(),
            expect={0: {"value": 10}, 1: {"value": 11}, 2: {"value": 12}},
        )

    def test_mutual_manual_override(self):
        from models.circular import MutualA, MutualB

        assert_reconciled(
            MutualA(value=100), MutualB(value=200),
            expect={0: {"value": 100}, 1: {"value": 200}},
        )
