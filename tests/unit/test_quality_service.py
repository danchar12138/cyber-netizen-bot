"""统一质量概览只读编排服务测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import QualityOverviewService
from cnb_domain import (
    EvaluationReport,
    ObservabilityAlertRecommendationQualityMetrics,
    QualityDataCoverage,
    QualityEvaluationScope,
)


class RecordingEvaluationReader:
    """记录查询作用域的拟人评测读取桩。"""

    def __init__(self, report: EvaluationReport) -> None:
        self.report = report
        self.calls: list[tuple[UUID, UUID]] = []

    async def get_report(self, *, tenant_id: UUID, reviewer_id: UUID) -> EvaluationReport:
        self.calls.append((tenant_id, reviewer_id))
        return self.report


class RecordingAlertRecommendationReader:
    """记录窗口和 Agent 作用域的告警质量读取桩。"""

    def __init__(self, metrics: ObservabilityAlertRecommendationQualityMetrics) -> None:
        self.metrics = metrics
        self.calls: list[tuple[UUID, UUID, int, str | None, datetime | None]] = []

    async def alert_recommendation_quality_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int = 10_080,
        source_type: str | None = None,
        now: datetime | None = None,
    ) -> ObservabilityAlertRecommendationQualityMetrics:
        self.calls.append((tenant_id, agent_id, window_minutes, source_type, now))
        return self.metrics


def _evaluation(*, has_data: bool) -> EvaluationReport:
    return EvaluationReport(
        total_runs=1 if has_data else 0,
        gate_passed_runs=1 if has_data else 0,
        latest_pass_rate=100.0 if has_data else None,
        pending_reviews=0,
        completed_reviews=0,
        candidate_wins=0,
        reference_wins=0,
        ties=0,
        candidate_average_score=None,
        reference_average_score=None,
    )


def _operations(*, has_data: bool, now: datetime) -> ObservabilityAlertRecommendationQualityMetrics:
    return ObservabilityAlertRecommendationQualityMetrics(
        window_started_at=now - timedelta(days=7),
        window_ended_at=now,
        total=1 if has_data else 0,
        accepted=1 if has_data else 0,
        rejected=0,
        acceptance_rate_percent=100.0 if has_data else 0.0,
        accepted_resolved=1 if has_data else 0,
        accepted_active=0,
        replay_total=0,
        replay_allowed=0,
        replay_blocked=0,
        actions=(),
        sources=(),
    )


@pytest.mark.parametrize(
    ("has_evaluation", "has_operations", "expected"),
    [
        (True, True, QualityDataCoverage.COMPLETE),
        (True, False, QualityDataCoverage.EVALUATION_ONLY),
        (False, True, QualityDataCoverage.OPERATIONS_ONLY),
        (False, False, QualityDataCoverage.EMPTY),
    ],
)
async def test_quality_overview_reports_real_coverage_and_shared_scope(
    has_evaluation: bool,
    has_operations: bool,
    expected: QualityDataCoverage,
) -> None:
    generated_at = datetime(2026, 9, 16, 8, 30, tzinfo=UTC)
    tenant_id, agent_id, reviewer_id = uuid4(), uuid4(), uuid4()
    evaluation_reader = RecordingEvaluationReader(_evaluation(has_data=has_evaluation))
    operations_reader = RecordingAlertRecommendationReader(
        _operations(has_data=has_operations, now=generated_at)
    )
    service = QualityOverviewService(
        evaluation_reader,
        operations_reader,
        agent_id=agent_id,
    )

    overview = await service.get_overview(
        tenant_id=tenant_id,
        reviewer_id=reviewer_id,
        window_minutes=2_880,
        now=generated_at,
    )

    assert overview.coverage is expected
    assert overview.generated_at == generated_at
    assert overview.evaluation_scope is QualityEvaluationScope.CURRENT_AGENT_ALL_HISTORY
    assert overview.operations_window_minutes == 2_880
    assert overview.automatic_actions_allowed is False
    assert evaluation_reader.calls == [(tenant_id, reviewer_id)]
    assert operations_reader.calls == [(tenant_id, agent_id, 2_880, None, generated_at)]
