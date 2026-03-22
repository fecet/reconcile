import sys
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from models.parallel import MoESpec, ParallelSpec, ScaleSpec
from models.training import (
    CompositeLoss,
    MAELoss,
    MSELoss,
    NeedsLoss,
    TrainingSpec,
)
from models.workflow import AdamWOptimizerSpec, WorkflowSpec

from reconcile import Unresolvable, dependency, reconcile


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
            loss=MSELoss(),
            workflow=WorkflowSpec(warmup_steps=100),
            training=TrainingSpec(num_steps=2000),
            optimizer=AdamWOptimizerSpec(lr=1e-3),
        ).expect(
            workflow={"num_steps": 2000, "lr": 1e-3},
            training={"num_steps": 2000},
            optimizer={"lr": 1e-3, "decay_steps": 200},
        )
        assert case.workflow.training is case.training
        assert case.optimizer.lr == case.workflow.lr

    def test_manual_override(self):
        reconcile_case(
            workflow=WorkflowSpec(warmup_steps=100, num_steps=999, lr=1e-2),
            training=TrainingSpec(num_steps=2000),
            optimizer=AdamWOptimizerSpec(lr=5e-4),
        ).expect(workflow={"num_steps": 999, "lr": 1e-2})

    def test_hitchhike_resolves_from_manually_set_field(self):
        reconcile_case(
            workflow=WorkflowSpec(
                warmup_steps=100,
                training=TrainingSpec(num_steps=500),
                num_steps=500,
                lr=0.01,
            ),
        ).expect(workflow={"num_steps": 500, "lr": 0.01})

    def test_model_fields_and_dump(self):
        assert "training" in WorkflowSpec.model_fields
        assert "num_steps" in WorkflowSpec.model_fields
        assert "lr" in WorkflowSpec.model_fields
        assert WorkflowSpec().model_dump() == {
            "warmup_steps": 0,
            "training": None,
            "num_steps": None,
            "lr": None,
        }

    def test_cycle_seeded(self):
        reconcile_case(
            training=TrainingSpec(global_batch_size=32),
            parallel=ParallelSpec(dp=4),
        ).expect(
            training={"global_batch_size": 32},
            parallel={"local_batch_size": 8},
        )
        reconcile_case(
            training=TrainingSpec(),
            parallel=ParallelSpec(dp=4, local_batch_size=8),
        ).expect(
            training={"global_batch_size": 32},
            parallel={"local_batch_size": 8},
        )

    def test_cycle_manual_override(self):
        reconcile_case(
            training=TrainingSpec(global_batch_size=64),
            parallel=ParallelSpec(local_batch_size=16),
        ).expect(
            training={"global_batch_size": 64},
            parallel={"local_batch_size": 16},
        )

    def test_cycle_converges_across_order(self):
        def values(*participants: Any) -> dict[str, int]:
            return {
                type(obj).__name__: obj.global_batch_size
                if hasattr(obj, "global_batch_size")
                else obj.local_batch_size
                for obj in reconcile(*participants)
            }

        assert values(TrainingSpec(global_batch_size=32), ParallelSpec(dp=4)) == values(
            ParallelSpec(dp=4), TrainingSpec(global_batch_size=32)
        )

    def test_ring_seeded(self):
        class R1(BaseModel):
            value: int = Field(default=0)

            @dependency(value)
            def _(self, r3: "R3") -> int:
                return r3.value + 1

        class R2(BaseModel):
            value: int = Field(default=0)

            @dependency(value)
            def _(self, r1: R1) -> int:
                return r1.value + 1

        class R3(BaseModel):
            value: int = Field(default=0)

            @dependency(value)
            def _(self, r2: R2) -> int:
                return r2.value + 1

        reconcile_case(
            r1=R1(value=10),
            r2=R2(),
            r3=R3(),
        ).expect(
            r1={"value": 10},
            r2={"value": 11},
            r3={"value": 12},
        )


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

    def test_cycle_detected(self):
        for participants in [
            (TrainingSpec(), ParallelSpec()),
            (ParallelSpec(), TrainingSpec()),
        ]:
            with pytest.raises(ValueError, match="Cycle detected"):
                reconcile(*participants)

    def test_ring_cycle(self):
        class R1(BaseModel):
            value: int = Field(default=0)

            @dependency(value)
            def _(self, r3: "R3") -> int:
                return r3.value + 1

        class R2(BaseModel):
            value: int = Field(default=0)

            @dependency(value)
            def _(self, r1: R1) -> int:
                return r1.value + 1

        class R3(BaseModel):
            value: int = Field(default=0)

            @dependency(value)
            def _(self, r2: R2) -> int:
                return r2.value + 1

        with pytest.raises(ValueError, match="Cycle detected"):
            reconcile(R1(), R2(), R3())

    def test_cycle_restores_classes(self):
        t = TrainingSpec()
        p = ParallelSpec()
        with pytest.raises(ValueError, match="Cycle detected"):
            reconcile(t, p)
        assert type(t).__name__ == "TrainingSpec"
        assert type(p).__name__ == "ParallelSpec"
        assert t.global_batch_size == 0
        assert p.local_batch_size == 0


class TestFeatures:
    def test_field_constraints_validated(self):
        with pytest.raises(ValueError, match="less than or equal to 10000"):
            reconcile(
                WorkflowSpec(), TrainingSpec(num_steps=99999), AdamWOptimizerSpec()
            )

        reconcile_case(
            workflow=WorkflowSpec(),
            training=TrainingSpec(num_steps=50),
            optimizer=AdamWOptimizerSpec(),
        ).expect(
            workflow={"num_steps": 50, "lr": 1e-3},
            optimizer={"decay_steps": 100},
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

    def test_nullable_provider(self):
        class NullableSpec(BaseModel):
            value: int | None = Field(default=1)

            @dependency(value)
            def _(self, t: TrainingSpec) -> int | None:
                return None

        (spec, _) = reconcile(NullableSpec(), TrainingSpec())
        assert spec.value is None
        (spec,) = reconcile(NullableSpec())
        assert spec.value == 1  # fallback to default

    def test_fallback_constraint_violation(self):
        class Constrained(BaseModel):
            value: int = Field(default=0, ge=1)

            @dependency(value)
            def _(self, t: TrainingSpec) -> int:
                return t.num_steps

        with pytest.raises(ValueError):
            reconcile(Constrained())

    def test_extra_fields_skipped_in_validation(self):
        class FlexibleWorkflow(WorkflowSpec):
            model_config = ConfigDict(extra="allow")

        obj = FlexibleWorkflow(custom_flag=True)
        (obj, _, _) = reconcile(obj, TrainingSpec(num_steps=42), AdamWOptimizerSpec())
        assert obj.num_steps == 42
        assert obj.custom_flag is True

    def test_multi_param_self_and_nested(self):
        scale = ScaleSpec(factor=0.5)
        parallel = ParallelSpec(moe=MoESpec(num_experts=4), local_batch_size=8)
        (scale, _, _) = reconcile(scale, TrainingSpec(num_steps=100), parallel)
        assert scale.scaled_steps == 200

    def test_cross_validator_with_nested_access(self):
        parallel = ParallelSpec(moe=MoESpec(num_experts=3), local_batch_size=8)
        with pytest.raises(ValueError, match="divisible by num_experts"):
            reconcile(parallel, TrainingSpec(num_steps=100))

        parallel = ParallelSpec(moe=MoESpec(num_experts=4), local_batch_size=8)
        reconcile(parallel, TrainingSpec(num_steps=100))


class TestHitchhike:
    def test_hitchhike_not_returned(self):
        training = TrainingSpec(num_steps=500)
        workflow = WorkflowSpec(
            warmup_steps=100,
            training=training,
            num_steps=500,
            lr=0.01,
        )
        results = reconcile(workflow)
        assert len(results) == 1
        assert results[0] is workflow

    def test_hitchhike_dedup_with_explicit(self):
        training = TrainingSpec(num_steps=500)
        workflow = WorkflowSpec(training=training, num_steps=500, lr=0.01)
        (w, t) = reconcile(workflow, training)
        assert w.num_steps == 500

    def test_composite_hitchhike_prefers_explicit(self):
        composite = CompositeLoss(mse=MSELoss(), mae=MAELoss())
        needs = NeedsLoss()
        (needs, composite) = reconcile(needs, composite)
        assert needs.name == "CompositeLoss"

    def test_nested_subconfig_hitchhikes(self):
        class NeedsMoE(BaseModel):
            expert_count: int = Field(default=0)

            @dependency(expert_count)
            def _(self, moe: MoESpec) -> int:
                return moe.num_experts

        parallel = ParallelSpec(moe=MoESpec(num_experts=16))
        needs = NeedsMoE()
        (needs, _) = reconcile(needs, parallel)
        assert needs.expert_count == 16

    def test_deep_hitchhike(self):
        class Inner(BaseModel):
            value: int = 42

        class Middle(BaseModel):
            inner: Inner

        class Outer(BaseModel):
            middle: Middle

        class Consumer(BaseModel):
            got: int = Field(default=0)

            @dependency(got)
            def _(self, i: Inner) -> int:
                return i.value

        outer = Outer(middle=Middle(inner=Inner(value=99)))
        consumer = Consumer()
        (consumer, _) = reconcile(consumer, outer)
        assert consumer.got == 99


@pytest.mark.skipif(sys.version_info < (3, 14), reason="PEP 649 deferred annotations")
class TestCrossFileForwardRef:
    def test_bare_annotation(self):
        from models.parallel import BareRefScale

        reconcile_case(
            s=BareRefScale(),
            t=TrainingSpec(num_steps=500),
        ).expect(
            s={"steps": 1000},
        )
