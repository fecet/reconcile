from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from models.training import (
    AdamWOptimizerSpec,
    CrossEntropyLoss,
    MAELoss,
    MSELoss,
    NeedsLoss,
    TrainingSpec,
    WorkflowSpec,
)

from reconcile import deferred, dependency, reconcile


class ReconcileCase(SimpleNamespace):
    def expect(self, **participants: dict[str, Any]) -> "ReconcileCase":
        for name, fields in participants.items():
            obj = getattr(self, name)
            for field, value in fields.items():
                assert getattr(obj, field) == value, (
                    f"{name}.{field}: expected {value!r}, got {getattr(obj, field)!r}"
                )
        return self


def reconcile_case(**participants: Any) -> ReconcileCase:
    results = reconcile(*participants.values())
    return ReconcileCase(**dict(zip(participants, results, strict=True)))


class TestResolution:
    def test_cross_object(self):
        case = reconcile_case(
            workflow=WorkflowSpec(warmup_steps=100),
            training=TrainingSpec(num_steps=2000),
            optimizer=AdamWOptimizerSpec(lr=1e-3),
        ).expect(
            workflow={
                    "num_steps": 2000,
                    "lr": 1e-3,
                    "batch_size": 2000,
                    "effective_lr": 1e-3,
                    "tags": ["steps=2000"],
                },
            training={"num_steps": 2000},
            optimizer={"lr": 1e-3},
        )
        assert case.workflow.training is case.training
        assert case.optimizer.lr == 1e-3

    def test_manual_override(self):
        reconcile_case(
            workflow=WorkflowSpec(
                warmup_steps=100,
                num_steps=999,
                lr=1e-2,
                effective_lr=5e-2,
                tags=["manual"],
            ),
            training=TrainingSpec(num_steps=2000),
            optimizer=AdamWOptimizerSpec(lr=5e-4),
        ).expect(
            workflow={
                "num_steps": 999,
                "lr": 1e-2,
                "effective_lr": 5e-2,
                "tags": ["manual"],
                "batch_size": 2000,
            }
        )

    def test_multi_participant(self):
        case = reconcile_case(
            loss=CrossEntropyLoss(),
            optimizer=AdamWOptimizerSpec(lr=3e-4),
            workflow=WorkflowSpec(warmup_steps=200),
            training=TrainingSpec(num_steps=5000),
        ).expect(
            optimizer={"lr": 3e-4},
            workflow={
                    "num_steps": 5000,
                    "lr": 3e-4,
                    "batch_size": 5000,
                    "effective_lr": 3e-4,
                    "tags": ["steps=5000"],
                },
        )
        assert case.loss("logits", "labels") == "ce_loss(ignore_index=-100)"
        assert case.workflow.training is case.training
        assert case.optimizer.lr == case.workflow.lr

    def test_skip_when_dependency_absent(self):
        reconcile_case(
            workflow=WorkflowSpec(
                warmup_steps=100,
                training=TrainingSpec(num_steps=500),
                num_steps=500,
                lr=0.01,
            ),
        ).expect(
            workflow={
                "num_steps": 500,
                "lr": 0.01,
                "batch_size": 32,
                "effective_lr": 0.001,
                "tags": [],
            }
        )

    def test_model_fields_and_dump(self):
        assert "training" in WorkflowSpec.model_fields
        assert "num_steps" in WorkflowSpec.model_fields
        assert "lr" in WorkflowSpec.model_fields
        assert WorkflowSpec().model_dump() == {
            "warmup_steps": 0,
            "lr_min": 0.0,
            "training": None,
            "num_steps": None,
            "lr": None,
            "batch_size": 32,
            "effective_lr": 0.001,
            "tags": [],
        }
        assert WorkflowSpec(training=TrainingSpec(), num_steps=42, lr=0.5).num_steps == 42
        assert WorkflowSpec(training=TrainingSpec(), num_steps=42, lr=0.5).lr == 0.5
        assert WorkflowSpec(training=TrainingSpec()).batch_size == 32


class TestErrors:
    def test_duplicate_type(self):
        with pytest.raises(TypeError, match="Ambiguous"):
            reconcile(TrainingSpec(), TrainingSpec(), AdamWOptimizerSpec())

    def test_required_unresolved(self):
        with pytest.raises(ValueError, match="required but unresolved"):
            reconcile(WorkflowSpec(warmup_steps=100))

    def test_derivation_validation_error(self):
        with pytest.raises(ValueError, match=r"warmup \(5000\) >= total \(2000\)"):
            reconcile(
                WorkflowSpec(warmup_steps=5000),
                TrainingSpec(num_steps=2000),
                AdamWOptimizerSpec(),
            )

    def test_fail_fast(self):
        with pytest.raises(ValueError):
            reconcile(
                AdamWOptimizerSpec(lr=0),
                WorkflowSpec(warmup_steps=5000),
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
            reconcile(WorkflowSpec(), TrainingSpec(num_steps=99999), AdamWOptimizerSpec())

        reconcile_case(
            workflow=WorkflowSpec(),
            training=TrainingSpec(num_steps=50),
            optimizer=AdamWOptimizerSpec(),
        ).expect(
            workflow={
                "batch_size": 50,
                "num_steps": 50,
                "lr": 1e-3,
            },
        )

    def test_subclass_resolution(self):
        case = reconcile_case(loss=MSELoss(), training=TrainingSpec()).expect(
            loss={"weight": 1.0}
        )
        assert isinstance(case.loss, MSELoss)

    def test_nested_model_field_resolution(self):
        training = TrainingSpec(num_steps=7)
        case = reconcile_case(
            workflow=WorkflowSpec(),
            training=training,
            optimizer=AdamWOptimizerSpec(),
        ).expect(
            workflow={
                "training": training,
                "num_steps": 7,
                "lr": 1e-3,
            }
        )
        assert case.workflow.training is case.training
        assert case.workflow.training.num_steps == 7

    def test_field_default_as_fallback(self):
        reconcile_case(
            workflow=WorkflowSpec(training=TrainingSpec(num_steps=5000), num_steps=5000, lr=0.01),
            training=TrainingSpec(num_steps=5000),
            optimizer=AdamWOptimizerSpec(lr=0.01),
        ).expect(
            workflow={
                "batch_size": 5000,
                "effective_lr": 0.01,
                "tags": ["steps=5000"],
            },
        )

        reconcile_case(
            workflow=WorkflowSpec(training=TrainingSpec(), num_steps=1000, lr=1e-3),
        ).expect(
            workflow={
                "batch_size": 32,
                "effective_lr": 0.001,
                "tags": [],
            },
        )

        reconcile_case(
            workflow=WorkflowSpec(
                training=TrainingSpec(num_steps=5000),
                num_steps=5000,
                lr=0.01,
                tags=["manual"],
            ),
            training=TrainingSpec(num_steps=5000),
            optimizer=AdamWOptimizerSpec(lr=0.01),
        ).expect(
            workflow={"tags": ["manual"]},
        )

    def test_fallback_constraint_violation(self):
        class Constrained(BaseModel):
            value: int = Field(default=0, ge=1)

            @dependency(value)
            def _(self, t: TrainingSpec) -> int:
                return t.num_steps

        with pytest.raises(ValueError):
            reconcile(Constrained())

    def test_fallback_valid_default_passes(self):
        class Constrained(BaseModel):
            value: int = Field(default=5, ge=1)

            @dependency(value)
            def _(self, t: TrainingSpec) -> int:
                return t.num_steps

        (c,) = reconcile(Constrained())
        assert c.value == 5

    def test_multiple_deps_on_field_rejected(self):
        with pytest.raises(TypeError, match="Multi.items: multiple providers"):
            class Multi(BaseModel):
                items: list[str] = deferred(default_factory=list)

                @dependency(items)
                def _a(self, t: TrainingSpec) -> list[str]:
                    return [f"steps={t.num_steps}"]

                @dependency(items)
                def _b(self, o: AdamWOptimizerSpec) -> list[str]:
                    return [f"lr={o.lr}"]


class TestCircular:
    def test_mutual_required_both_unset(self):
        from models.circular import MutualA, MutualB

        with pytest.raises(
            ValueError,
            match=r"Cycle detected: MutualA\.value -> MutualB\.value -> MutualA\.value",
        ):
            reconcile(MutualA(), MutualB())

    def test_mutual_required_one_seeded(self):
        from models.circular import MutualA, MutualB

        reconcile_case(
            a=MutualA(value=5),
            b=MutualB(),
        ).expect(
            a={"value": 5},
            b={"value": 6},
        )
        reconcile_case(
            a=MutualA(),
            b=MutualB(value=10),
        ).expect(
            a={"value": 11},
            b={"value": 10},
        )

    def test_mutual_with_defaults(self):
        from models.circular import NodeX, NodeY

        with pytest.raises(
            ValueError,
            match=r"Cycle detected: NodeX\.value -> NodeY\.value -> NodeX\.value",
        ):
            reconcile(NodeX(), NodeY())

    def test_cycle_errors_do_not_depend_on_participant_order(self):
        from models.circular import MutualA, MutualB, NodeX, NodeY

        for participants in [
            (MutualA(), MutualB()),
            (MutualB(), MutualA()),
            (NodeX(), NodeY()),
            (NodeY(), NodeX()),
        ]:
            with pytest.raises(ValueError, match="Cycle detected"):
                reconcile(*participants)

    def test_ring_no_seed(self):
        from models.circular import Ring1, Ring2, Ring3

        with pytest.raises(
            ValueError,
            match=r"Cycle detected: Ring1\.value -> Ring3\.value -> Ring2\.value -> Ring1\.value",
        ):
            reconcile(Ring1(), Ring2(), Ring3())

    def test_ring_one_seeded(self):
        from models.circular import Ring1, Ring2, Ring3

        reconcile_case(
            r1=Ring1(value=10),
            r2=Ring2(),
            r3=Ring3(),
        ).expect(
            r1={"value": 10},
            r2={"value": 11},
            r3={"value": 12},
        )

    def test_seeded_cycles_converge_to_same_values_across_order(self):
        from models.circular import MutualA, MutualB, Ring1, Ring2, Ring3

        def values_by_type(*participants: Any) -> dict[str, int]:
            return {type(obj).__name__: obj.value for obj in reconcile(*participants)}

        assert values_by_type(MutualA(value=5), MutualB()) == values_by_type(
            MutualB(),
            MutualA(value=5),
        )
        assert values_by_type(Ring1(value=10), Ring2(), Ring3()) == values_by_type(
            Ring2(),
            Ring3(),
            Ring1(value=10),
        )

    def test_mutual_manual_override(self):
        from models.circular import MutualA, MutualB

        reconcile_case(
            a=MutualA(value=100),
            b=MutualB(value=200),
        ).expect(
            a={"value": 100},
            b={"value": 200},
        )

    def test_cycle_error_restores_original_classes(self):
        from models.circular import MutualA, MutualB

        a = MutualA()
        b = MutualB()
        with pytest.raises(ValueError, match="Cycle detected"):
            reconcile(a, b)
        assert type(a).__name__ == "MutualA"
        assert type(b).__name__ == "MutualB"
        assert a.value is None
        assert b.value is None
