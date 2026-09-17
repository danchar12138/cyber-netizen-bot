"""不可变拟人评测决策记录服务测试。"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    EvaluationDecisionService,
    EvaluationDecisionValidationError,
    EvaluationQualityHistoryService,
)
from cnb_domain import (
    BlindReviewPreference,
    EvaluationDecisionOutcome,
    EvaluationDecisionReason,
    EvaluationQualityReviewSample,
    EvaluationQualityRunSample,
    EvaluationQualitySamples,
    EvaluationVersionSnapshot,
)
from cnb_infrastructure import MemoryEvaluationRepository


class QualitySampleReader:
    def __init__(self, samples: EvaluationQualitySamples) -> None:
        self.samples = samples

    async def get_quality_samples(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> EvaluationQualitySamples:
        del tenant_id, window_started_at, window_ended_at
        return self.samples


def _snapshot(*, prompt_version: int, model: str = "model-a") -> EvaluationVersionSnapshot:
    return EvaluationVersionSnapshot(
        suite_key="anthropomorphic",
        suite_version=1,
        configuration_version=1,
        persona_version=1,
        prompt_version=prompt_version,
        policy_version=1,
        model_route_version=1,
        provider="openai",
        model=model,
    )


def _samples(
    *, runs_per_snapshot: int = 5, reviews_per_snapshot: int = 5
) -> tuple[EvaluationQualitySamples, EvaluationVersionSnapshot, EvaluationVersionSnapshot]:
    now = datetime.now(UTC)
    candidate, baseline = _snapshot(prompt_version=2), _snapshot(prompt_version=1)
    runs: list[EvaluationQualityRunSample] = []
    reviews: list[EvaluationQualityReviewSample] = []
    for snapshot_index, snapshot in enumerate((baseline, candidate)):
        for index in range(runs_per_snapshot):
            run_id = uuid4()
            created_at = now - timedelta(days=4 - snapshot_index * 2, minutes=index)
            runs.append(
                EvaluationQualityRunSample(
                    run_id=run_id,
                    created_at=created_at,
                    gate_passed=snapshot is candidate,
                    pass_rate=90.0 if snapshot is candidate else 70.0,
                    snapshot=snapshot,
                )
            )
            if index < reviews_per_snapshot:
                reviews.append(
                    EvaluationQualityReviewSample(
                        run_id=run_id,
                        preference=BlindReviewPreference.CANDIDATE,
                        candidate_average_score=4.5 if snapshot is candidate else 3.5,
                        reference_average_score=3.0,
                        created_at=created_at + timedelta(minutes=1),
                    )
                )
    return EvaluationQualitySamples(runs=tuple(runs), reviews=tuple(reviews)), candidate, baseline


async def test_decision_freezes_safe_canonical_report_and_stable_bytes() -> None:
    samples, candidate, baseline = _samples()
    repository = MemoryEvaluationRepository()
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    service = EvaluationDecisionService(
        repository,
        EvaluationQualityHistoryService(QualitySampleReader(samples)),
        agent_id=agent_id,
    )

    created = await service.create_decision(
        tenant_id=tenant_id,
        actor_id=actor_id,
        window_minutes=10_080,
        candidate=candidate,
        baseline=baseline,
        outcome=EvaluationDecisionOutcome.ADOPT_CANDIDATE,
        reason=EvaluationDecisionReason.QUALITY_GAIN,
    )
    loaded = await service.get_decision(decision_id=created.id, tenant_id=tenant_id)
    report = json.loads(created.content)

    assert loaded.content == created.content
    assert loaded.sha256 == hashlib.sha256(created.content).hexdigest()
    assert report["comparison"]["candidate"]["snapshot"]["prompt_version"] == 2
    assert report["comparison"]["pass_rate_delta_percentage_points"] == 20.0
    assert report["statistical_significance_assessed"] is False
    assert report["causal_conclusion_allowed"] is False
    assert report["automatic_actions_allowed"] is False
    decoded = created.content.decode("utf-8")
    assert all(
        forbidden not in decoded
        for forbidden in (
            "input_text",
            "candidate_response",
            "reference_response",
            '"note":',
            '"prompt":',
        )
    )


async def test_decision_rejects_non_waiting_outcome_when_evidence_is_incomplete() -> None:
    samples, candidate, baseline = _samples(runs_per_snapshot=4, reviews_per_snapshot=4)
    service = EvaluationDecisionService(
        MemoryEvaluationRepository(),
        EvaluationQualityHistoryService(QualitySampleReader(samples)),
        agent_id=uuid4(),
    )

    with pytest.raises(EvaluationDecisionValidationError, match="样本均须达到门槛"):
        await service.create_decision(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            window_minutes=10_080,
            candidate=candidate,
            baseline=baseline,
            outcome=EvaluationDecisionOutcome.KEEP_BASELINE,
            reason=EvaluationDecisionReason.REGRESSION_RISK,
        )


async def test_decision_rejects_cross_model_snapshot() -> None:
    samples, candidate, baseline = _samples()
    other_model = _snapshot(prompt_version=1, model="model-b")
    samples = EvaluationQualitySamples(
        runs=tuple(
            EvaluationQualityRunSample(
                run_id=item.run_id,
                created_at=item.created_at,
                gate_passed=item.gate_passed,
                pass_rate=item.pass_rate,
                snapshot=other_model if item.snapshot == baseline else item.snapshot,
            )
            for item in samples.runs
        ),
        reviews=samples.reviews,
    )
    service = EvaluationDecisionService(
        MemoryEvaluationRepository(),
        EvaluationQualityHistoryService(QualitySampleReader(samples)),
        agent_id=uuid4(),
    )

    with pytest.raises(EvaluationDecisionValidationError, match="同源完整快照"):
        await service.create_decision(
            tenant_id=uuid4(),
            actor_id=uuid4(),
            window_minutes=10_080,
            candidate=candidate,
            baseline=other_model,
            outcome=EvaluationDecisionOutcome.WAIT_FOR_EVIDENCE,
            reason=EvaluationDecisionReason.MANUAL_REVIEW,
        )
