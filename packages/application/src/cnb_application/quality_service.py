"""拟人评测与告警运营质量事实的只读编排服务。"""

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from cnb_domain import (
    BlindReviewPreference,
    EvaluationQualityBaselineComparison,
    EvaluationQualityComparisonKey,
    EvaluationQualityHistory,
    EvaluationQualityReviewSample,
    EvaluationQualityRunSample,
    EvaluationQualitySamples,
    EvaluationQualityTrendPoint,
    EvaluationReport,
    EvaluationVersionQualitySummary,
    EvaluationVersionSnapshot,
    ObservabilityAlertRecommendationQualityMetrics,
    QualityDataCoverage,
    QualityEvaluationScope,
    QualityReviewAttribution,
    UnifiedQualityOverview,
)


class QualityHistoryValidationError(ValueError):
    """质量历史查询窗口或时间桶不合法时抛出。"""


@dataclass(frozen=True, slots=True)
class _EvaluationQualityMetrics:
    """趋势桶与版本摘要共享的内部质量指标。"""

    total_runs: int
    gate_passed_runs: int
    average_pass_rate: float | None
    completed_reviews: int
    candidate_wins: int
    reference_wins: int
    ties: int
    candidate_average_score: float | None
    reference_average_score: float | None


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


class EvaluationQualityHistoryReader(Protocol):
    """按当前 Agent 和有界时间范围读取安全评测样本的端口。"""

    async def get_quality_samples(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> EvaluationQualitySamples: ...


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


class EvaluationQualityHistoryService:
    """将安全评测样本聚合为连续趋势和完整冻结版本摘要。"""

    _MINIMUM_WINDOW_MINUTES = 1_440
    _MAXIMUM_WINDOW_MINUTES = 129_600
    _MINIMUM_BUCKET_MINUTES = 60
    _MAXIMUM_BUCKETS = 90
    _MINIMUM_COMPARISON_RUNS = 5
    _MINIMUM_COMPARISON_REVIEWS = 5

    def __init__(self, reader: EvaluationQualityHistoryReader) -> None:
        self._reader = reader

    async def get_history(
        self,
        *,
        tenant_id: UUID,
        window_minutes: int = 43_200,
        bucket_minutes: int = 7_200,
        now: datetime | None = None,
    ) -> EvaluationQualityHistory:
        """按运行发生时间归桶，并将盲评归属到对应运行。"""
        self._validate_window(window_minutes=window_minutes, bucket_minutes=bucket_minutes)
        generated_at = now or datetime.now(UTC)
        if generated_at.tzinfo is None:
            raise QualityHistoryValidationError("质量历史生成时间必须包含时区")
        generated_at = generated_at.astimezone(UTC)
        window_started_at = generated_at - timedelta(minutes=window_minutes)
        samples = await self._reader.get_quality_samples(
            tenant_id=tenant_id,
            window_started_at=window_started_at,
            window_ended_at=generated_at,
        )
        runs = tuple(
            item
            for item in samples.runs
            if window_started_at <= item.created_at.astimezone(UTC) < generated_at
        )
        run_ids = {item.run_id for item in runs}
        reviews = tuple(
            item
            for item in samples.reviews
            if item.run_id in run_ids and item.created_at.astimezone(UTC) < generated_at
        )
        reviews_by_run: dict[UUID, list[EvaluationQualityReviewSample]] = defaultdict(list)
        for review in reviews:
            reviews_by_run[review.run_id].append(review)

        bucket_count = window_minutes // bucket_minutes
        bucket_delta = timedelta(minutes=bucket_minutes)
        bucket_runs: list[list[EvaluationQualityRunSample]] = [[] for _ in range(bucket_count)]
        for run in runs:
            elapsed = run.created_at.astimezone(UTC) - window_started_at
            bucket_index = int(elapsed.total_seconds() // bucket_delta.total_seconds())
            bucket_runs[bucket_index].append(run)

        trend = tuple(
            self._trend_point(
                bucket_started_at=window_started_at + index * bucket_delta,
                bucket_ended_at=window_started_at + (index + 1) * bucket_delta,
                runs=items,
                reviews=self._reviews_for_runs(items, reviews_by_run),
            )
            for index, items in enumerate(bucket_runs)
        )

        runs_by_snapshot: dict[EvaluationVersionSnapshot, list[EvaluationQualityRunSample]] = (
            defaultdict(list)
        )
        for run in runs:
            runs_by_snapshot[run.snapshot].append(run)
        versions = tuple(
            sorted(
                (
                    self._version_summary(
                        snapshot=snapshot,
                        runs=items,
                        reviews=self._reviews_for_runs(items, reviews_by_run),
                    )
                    for snapshot, items in runs_by_snapshot.items()
                ),
                key=self._version_order_key,
                reverse=True,
            )
        )
        return EvaluationQualityHistory(
            generated_at=generated_at,
            window_started_at=window_started_at,
            window_ended_at=generated_at,
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
            review_attribution=QualityReviewAttribution.RUN_CREATED_AT,
            total_runs=len(runs),
            completed_reviews=len(reviews),
            trend=trend,
            versions=versions,
            baseline_comparisons=self._baseline_comparisons(versions),
            comparable_versions=len(versions) >= 2,
        )

    @classmethod
    def _validate_window(cls, *, window_minutes: int, bucket_minutes: int) -> None:
        if not cls._MINIMUM_WINDOW_MINUTES <= window_minutes <= cls._MAXIMUM_WINDOW_MINUTES:
            raise QualityHistoryValidationError("质量历史窗口必须在 1 天到 90 天之间")
        if not cls._MINIMUM_BUCKET_MINUTES <= bucket_minutes <= window_minutes:
            raise QualityHistoryValidationError("质量历史桶宽必须在 1 小时到查询窗口之间")
        if window_minutes % bucket_minutes:
            raise QualityHistoryValidationError("质量历史窗口必须可以被桶宽整除")
        if window_minutes // bucket_minutes > cls._MAXIMUM_BUCKETS:
            raise QualityHistoryValidationError("质量历史时间桶不能超过 90 个")

    @staticmethod
    def _reviews_for_runs(
        runs: Sequence[EvaluationQualityRunSample],
        reviews_by_run: dict[UUID, list[EvaluationQualityReviewSample]],
    ) -> tuple[EvaluationQualityReviewSample, ...]:
        return tuple(review for run in runs for review in reviews_by_run.get(run.run_id, ()))

    @classmethod
    def _trend_point(
        cls,
        *,
        bucket_started_at: datetime,
        bucket_ended_at: datetime,
        runs: Sequence[EvaluationQualityRunSample],
        reviews: Sequence[EvaluationQualityReviewSample],
    ) -> EvaluationQualityTrendPoint:
        metrics = cls._metrics(runs=runs, reviews=reviews)
        return EvaluationQualityTrendPoint(
            bucket_started_at=bucket_started_at,
            bucket_ended_at=bucket_ended_at,
            total_runs=metrics.total_runs,
            gate_passed_runs=metrics.gate_passed_runs,
            average_pass_rate=metrics.average_pass_rate,
            completed_reviews=metrics.completed_reviews,
            candidate_wins=metrics.candidate_wins,
            reference_wins=metrics.reference_wins,
            ties=metrics.ties,
            candidate_average_score=metrics.candidate_average_score,
            reference_average_score=metrics.reference_average_score,
        )

    @classmethod
    def _version_summary(
        cls,
        *,
        snapshot: EvaluationVersionSnapshot,
        runs: Sequence[EvaluationQualityRunSample],
        reviews: Sequence[EvaluationQualityReviewSample],
    ) -> EvaluationVersionQualitySummary:
        metrics = cls._metrics(runs=runs, reviews=reviews)
        if metrics.average_pass_rate is None:
            raise ValueError("冻结版本摘要至少需要一个运行样本")
        return EvaluationVersionQualitySummary(
            snapshot=snapshot,
            first_run_at=min(item.created_at for item in runs),
            latest_run_at=max(item.created_at for item in runs),
            total_runs=metrics.total_runs,
            gate_passed_runs=metrics.gate_passed_runs,
            average_pass_rate=metrics.average_pass_rate,
            completed_reviews=metrics.completed_reviews,
            candidate_wins=metrics.candidate_wins,
            reference_wins=metrics.reference_wins,
            ties=metrics.ties,
            candidate_average_score=metrics.candidate_average_score,
            reference_average_score=metrics.reference_average_score,
        )

    @staticmethod
    def _version_order_key(
        item: EvaluationVersionQualitySummary,
    ) -> tuple[datetime, datetime, str, int, int, int, int, int, int, str, str]:
        """为冻结快照提供不依赖仓储返回顺序的确定性排序键。"""
        snapshot = item.snapshot
        return (
            item.latest_run_at,
            item.first_run_at,
            snapshot.suite_key,
            snapshot.suite_version,
            snapshot.configuration_version,
            snapshot.persona_version,
            snapshot.prompt_version,
            snapshot.policy_version,
            snapshot.model_route_version,
            snapshot.provider,
            snapshot.model,
        )

    @classmethod
    def _baseline_comparisons(
        cls,
        versions: Sequence[EvaluationVersionQualitySummary],
    ) -> tuple[EvaluationQualityBaselineComparison, ...]:
        """每个同源组只比较最新快照与紧邻的上一快照。"""
        groups: dict[
            EvaluationQualityComparisonKey,
            list[EvaluationVersionQualitySummary],
        ] = defaultdict(list)
        for version in versions:
            snapshot = version.snapshot
            key = EvaluationQualityComparisonKey(
                suite_key=snapshot.suite_key,
                suite_version=snapshot.suite_version,
                provider=snapshot.provider,
                model=snapshot.model,
            )
            groups[key].append(version)

        comparisons: list[EvaluationQualityBaselineComparison] = []
        for key, group in groups.items():
            ordered = sorted(group, key=cls._version_order_key, reverse=True)
            if len(ordered) < 2:
                continue
            comparisons.append(
                cls._baseline_comparison(
                    key=key,
                    candidate=ordered[0],
                    baseline=ordered[1],
                )
            )
        return tuple(
            sorted(
                comparisons,
                key=lambda item: (
                    item.candidate.latest_run_at,
                    item.key.suite_key,
                    item.key.suite_version,
                    item.key.provider,
                    item.key.model,
                ),
                reverse=True,
            )
        )

    @classmethod
    def _baseline_comparison(
        cls,
        *,
        key: EvaluationQualityComparisonKey,
        candidate: EvaluationVersionQualitySummary,
        baseline: EvaluationVersionQualitySummary,
    ) -> EvaluationQualityBaselineComparison:
        automatic_regression_comparable = (
            candidate.total_runs >= cls._MINIMUM_COMPARISON_RUNS
            and baseline.total_runs >= cls._MINIMUM_COMPARISON_RUNS
        )
        blind_review_comparable = (
            candidate.completed_reviews >= cls._MINIMUM_COMPARISON_REVIEWS
            and baseline.completed_reviews >= cls._MINIMUM_COMPARISON_REVIEWS
        )
        return EvaluationQualityBaselineComparison(
            key=key,
            candidate=candidate,
            baseline=baseline,
            minimum_runs_per_snapshot=cls._MINIMUM_COMPARISON_RUNS,
            minimum_reviews_per_snapshot=cls._MINIMUM_COMPARISON_REVIEWS,
            automatic_regression_comparable=automatic_regression_comparable,
            blind_review_comparable=blind_review_comparable,
            pass_rate_delta_percentage_points=(
                round(candidate.average_pass_rate - baseline.average_pass_rate, 4)
                if automatic_regression_comparable
                else None
            ),
            candidate_average_score_delta=cls._score_delta(
                candidate.candidate_average_score,
                baseline.candidate_average_score,
                comparable=blind_review_comparable,
            ),
            reference_average_score_delta=cls._score_delta(
                candidate.reference_average_score,
                baseline.reference_average_score,
                comparable=blind_review_comparable,
            ),
        )

    @staticmethod
    def _score_delta(
        candidate: float | None,
        baseline: float | None,
        *,
        comparable: bool,
    ) -> float | None:
        if not comparable or candidate is None or baseline is None:
            return None
        return round(candidate - baseline, 4)

    @staticmethod
    def _metrics(
        *,
        runs: Sequence[EvaluationQualityRunSample],
        reviews: Sequence[EvaluationQualityReviewSample],
    ) -> _EvaluationQualityMetrics:
        candidate_scores = [item.candidate_average_score for item in reviews]
        reference_scores = [item.reference_average_score for item in reviews]
        return _EvaluationQualityMetrics(
            total_runs=len(runs),
            gate_passed_runs=sum(item.gate_passed for item in runs),
            average_pass_rate=(sum(item.pass_rate for item in runs) / len(runs) if runs else None),
            completed_reviews=len(reviews),
            candidate_wins=sum(
                item.preference is BlindReviewPreference.CANDIDATE for item in reviews
            ),
            reference_wins=sum(
                item.preference is BlindReviewPreference.REFERENCE for item in reviews
            ),
            ties=sum(item.preference is BlindReviewPreference.TIE for item in reviews),
            candidate_average_score=(
                sum(candidate_scores) / len(candidate_scores) if candidate_scores else None
            ),
            reference_average_score=(
                sum(reference_scores) / len(reference_scores) if reference_scores else None
            ),
        )
