"""拟人评测与告警运营质量事实的只读编排服务。"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from cnb_domain import (
    EvaluationReport,
    ObservabilityAlertRecommendationQualityMetrics,
    QualityDataCoverage,
    QualityEvaluationScope,
    UnifiedQualityOverview,
)


class EvaluationQualityReader(Protocol):
    """读取当前 Agent 全历史拟人评测聚合的端口。"""

    async def get_report(self, *, tenant_id: UUID, reviewer_id: UUID) -> EvaluationReport: ...


class AlertRecommendationQualityReader(Protocol):
    """读取当前 Agent 有界告警运营聚合的端口。"""

    async def alert_recommendation_quality_metrics(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        window_minutes: int = 10_080,
        source_type: str | None = None,
        now: datetime | None = None,
    ) -> ObservabilityAlertRecommendationQualityMetrics: ...


class QualityOverviewService:
    """并发组合两类既有安全聚合，不触发调参、发布或告警处置。"""

    def __init__(
        self,
        evaluation_reader: EvaluationQualityReader,
        alert_recommendation_reader: AlertRecommendationQualityReader,
        *,
        agent_id: UUID,
    ) -> None:
        self._evaluation_reader = evaluation_reader
        self._alert_recommendation_reader = alert_recommendation_reader
        self._agent_id = agent_id

    async def get_overview(
        self,
        *,
        tenant_id: UUID,
        reviewer_id: UUID,
        window_minutes: int = 10_080,
        now: datetime | None = None,
    ) -> UnifiedQualityOverview:
        """使用同一生成时点读取全历史评测和有界告警运营事实。"""
        generated_at = (now or datetime.now(UTC)).astimezone(UTC)
        evaluation, alert_recommendations = await asyncio.gather(
            self._evaluation_reader.get_report(
                tenant_id=tenant_id,
                reviewer_id=reviewer_id,
            ),
            self._alert_recommendation_reader.alert_recommendation_quality_metrics(
                tenant_id=tenant_id,
                agent_id=self._agent_id,
                window_minutes=window_minutes,
                now=generated_at,
            ),
        )
        return UnifiedQualityOverview(
            generated_at=generated_at,
            evaluation_scope=QualityEvaluationScope.CURRENT_AGENT_ALL_HISTORY,
            operations_window_minutes=window_minutes,
            coverage=self._coverage(evaluation, alert_recommendations),
            evaluation=evaluation,
            alert_recommendations=alert_recommendations,
        )

    @staticmethod
    def _coverage(
        evaluation: EvaluationReport,
        alert_recommendations: ObservabilityAlertRecommendationQualityMetrics,
    ) -> QualityDataCoverage:
        has_evaluation = evaluation.total_runs > 0 or evaluation.completed_reviews > 0
        has_operations = alert_recommendations.total > 0 or alert_recommendations.replay_total > 0
        if has_evaluation and has_operations:
            return QualityDataCoverage.COMPLETE
        if has_evaluation:
            return QualityDataCoverage.EVALUATION_ONLY
        if has_operations:
            return QualityDataCoverage.OPERATIONS_ONLY
        return QualityDataCoverage.EMPTY
