"""统一质量概览只读编排服务测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    EvaluationQualityHistoryService,
    QualityHistoryValidationError,
    QualityOverviewService,
)
from cnb_domain import (
    BlindReviewPreference,
    EvaluationQualityReviewSample,
    EvaluationQualityRunSample,
    EvaluationQualitySamples,
    EvaluationReport,
    EvaluationVersionSnapshot,
    ObservabilityAlertRecommendationQualityMetrics,
    QualityDataCoverage,
    QualityEvaluationScope,
    QualityReviewAttribution,
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


class RecordingQualityHistoryReader:
    """记录窗口边界并返回安全评测样本的读取桩。"""

    def __init__(self, samples: EvaluationQualitySamples) -> None:
        self.samples = samples
        self.calls: list[tuple[UUID, datetime, datetime]] = []

    async def get_quality_samples(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> EvaluationQualitySamples:
        self.calls.append((tenant_id, window_started_at, window_ended_at))
        return self.samples


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


async def test_quality_history_keeps_empty_buckets_and_groups_complete_snapshots() -> None:
    """趋势保留空桶，盲评按运行归属，版本按完整冻结快照分组。"""
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    started_at = now - timedelta(days=7)
    tenant_id = uuid4()
    first_run_id, second_run_id = uuid4(), uuid4()
    first_snapshot = EvaluationVersionSnapshot(
        suite_key="anthropomorphic-baseline",
        suite_version=1,
        configuration_version=3,
        persona_version=4,
        prompt_version=5,
        policy_version=6,
        model_route_version=7,
        provider="openai-compatible",
        model="model-a",
    )
    second_snapshot = EvaluationVersionSnapshot(
        suite_key="anthropomorphic-baseline",
        suite_version=1,
        configuration_version=3,
        persona_version=5,
        prompt_version=5,
        policy_version=6,
        model_route_version=7,
        provider="openai-compatible",
        model="model-a",
    )
    reader = RecordingQualityHistoryReader(
        EvaluationQualitySamples(
            runs=(
                EvaluationQualityRunSample(
                    run_id=first_run_id,
                    created_at=started_at + timedelta(hours=1),
                    gate_passed=True,
                    pass_rate=100.0,
                    snapshot=first_snapshot,
                ),
                EvaluationQualityRunSample(
                    run_id=second_run_id,
                    created_at=started_at + timedelta(days=2, hours=1),
                    gate_passed=False,
                    pass_rate=60.0,
                    snapshot=second_snapshot,
                ),
            ),
            reviews=(
                EvaluationQualityReviewSample(
                    run_id=first_run_id,
                    preference=BlindReviewPreference.CANDIDATE,
                    candidate_average_score=4.5,
                    reference_average_score=3.25,
                    created_at=now - timedelta(hours=1),
                ),
                EvaluationQualityReviewSample(
                    run_id=second_run_id,
                    preference=BlindReviewPreference.TIE,
                    candidate_average_score=4.0,
                    reference_average_score=4.0,
                    created_at=now - timedelta(minutes=30),
                ),
            ),
        )
    )

    history = await EvaluationQualityHistoryService(reader).get_history(
        tenant_id=tenant_id,
        window_minutes=10_080,
        bucket_minutes=1_440,
        now=now,
    )

    assert reader.calls == [(tenant_id, started_at, now)]
    assert history.review_attribution is QualityReviewAttribution.RUN_CREATED_AT
    assert history.total_runs == history.completed_reviews == 2
    assert len(history.trend) == 7
    assert history.trend[0].total_runs == 1
    assert history.trend[0].completed_reviews == 1
    assert history.trend[0].candidate_wins == 1
    assert history.trend[1].total_runs == 0
    assert history.trend[1].average_pass_rate is None
    assert history.trend[2].ties == 1
    assert len(history.versions) == 2
    assert history.versions[0].snapshot == second_snapshot
    assert history.versions[1].snapshot == first_snapshot
    assert len(history.baseline_comparisons) == 1
    assert history.baseline_comparisons[0].candidate.snapshot == second_snapshot
    assert history.baseline_comparisons[0].baseline.snapshot == first_snapshot
    assert history.baseline_comparisons[0].automatic_regression_comparable is False
    assert history.baseline_comparisons[0].blind_review_comparable is False
    assert history.comparable_versions is True
    assert history.automatic_actions_allowed is False


def _quality_snapshot(
    *,
    prompt_version: int,
    suite_version: int = 1,
    model: str = "model-a",
) -> EvaluationVersionSnapshot:
    return EvaluationVersionSnapshot(
        suite_key="anthropomorphic-baseline",
        suite_version=suite_version,
        configuration_version=3,
        persona_version=4,
        prompt_version=prompt_version,
        policy_version=6,
        model_route_version=7,
        provider="openai-compatible",
        model=model,
    )


def _append_snapshot_samples(
    *,
    runs: list[EvaluationQualityRunSample],
    reviews: list[EvaluationQualityReviewSample],
    snapshot: EvaluationVersionSnapshot,
    started_at: datetime,
    run_count: int,
    review_count: int,
    pass_rate: float,
    candidate_score: float,
    reference_score: float,
) -> None:
    run_ids: list[UUID] = []
    for index in range(run_count):
        run_id = uuid4()
        run_ids.append(run_id)
        runs.append(
            EvaluationQualityRunSample(
                run_id=run_id,
                created_at=started_at + timedelta(minutes=index),
                gate_passed=pass_rate >= 80,
                pass_rate=pass_rate,
                snapshot=snapshot,
            )
        )
    for index in range(review_count):
        reviews.append(
            EvaluationQualityReviewSample(
                run_id=run_ids[index % len(run_ids)],
                preference=BlindReviewPreference.CANDIDATE,
                candidate_average_score=candidate_score,
                reference_average_score=reference_score,
                created_at=started_at + timedelta(hours=1, minutes=index),
            )
        )


async def test_quality_history_compares_only_latest_and_adjacent_same_source_snapshots() -> None:
    """同源组只比较最新与紧邻快照，并在样本充足时输出观察差值。"""
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    runs: list[EvaluationQualityRunSample] = []
    reviews: list[EvaluationQualityReviewSample] = []
    oldest = _quality_snapshot(prompt_version=1)
    baseline = _quality_snapshot(prompt_version=2)
    candidate = _quality_snapshot(prompt_version=3)
    _append_snapshot_samples(
        runs=runs,
        reviews=reviews,
        snapshot=oldest,
        started_at=now - timedelta(days=6),
        run_count=5,
        review_count=5,
        pass_rate=50.0,
        candidate_score=2.0,
        reference_score=4.0,
    )
    _append_snapshot_samples(
        runs=runs,
        reviews=reviews,
        snapshot=baseline,
        started_at=now - timedelta(days=4),
        run_count=5,
        review_count=5,
        pass_rate=70.0,
        candidate_score=3.0,
        reference_score=4.0,
    )
    _append_snapshot_samples(
        runs=runs,
        reviews=reviews,
        snapshot=candidate,
        started_at=now - timedelta(days=2),
        run_count=5,
        review_count=5,
        pass_rate=90.0,
        candidate_score=4.25,
        reference_score=3.5,
    )

    history = await EvaluationQualityHistoryService(
        RecordingQualityHistoryReader(
            EvaluationQualitySamples(runs=tuple(runs), reviews=tuple(reviews))
        )
    ).get_history(
        tenant_id=uuid4(),
        window_minutes=10_080,
        bucket_minutes=1_440,
        now=now,
    )

    assert len(history.baseline_comparisons) == 1
    comparison = history.baseline_comparisons[0]
    assert comparison.candidate.snapshot == candidate
    assert comparison.baseline.snapshot == baseline
    assert comparison.baseline.snapshot != oldest
    assert comparison.minimum_runs_per_snapshot == 5
    assert comparison.minimum_reviews_per_snapshot == 5
    assert comparison.automatic_regression_comparable is True
    assert comparison.blind_review_comparable is True
    assert comparison.pass_rate_delta_percentage_points == 20.0
    assert comparison.candidate_average_score_delta == 1.25
    assert comparison.reference_average_score_delta == -0.5
    assert comparison.statistical_significance_assessed is False
    assert comparison.causal_conclusion_allowed is False


async def test_quality_history_does_not_compare_different_suite_versions_or_models() -> None:
    """评测集版本或模型任一不同，都不能形成同源基线。"""
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    runs: list[EvaluationQualityRunSample] = []
    reviews: list[EvaluationQualityReviewSample] = []
    for index, snapshot in enumerate(
        (
            _quality_snapshot(prompt_version=1),
            _quality_snapshot(prompt_version=2, suite_version=2),
            _quality_snapshot(prompt_version=3, model="model-b"),
        )
    ):
        _append_snapshot_samples(
            runs=runs,
            reviews=reviews,
            snapshot=snapshot,
            started_at=now - timedelta(days=6 - index),
            run_count=1,
            review_count=0,
            pass_rate=80.0,
            candidate_score=4.0,
            reference_score=3.0,
        )

    history = await EvaluationQualityHistoryService(
        RecordingQualityHistoryReader(
            EvaluationQualitySamples(runs=tuple(runs), reviews=tuple(reviews))
        )
    ).get_history(
        tenant_id=uuid4(),
        window_minutes=10_080,
        bucket_minutes=1_440,
        now=now,
    )

    assert len(history.versions) == 3
    assert history.baseline_comparisons == ()


@pytest.mark.parametrize(
    ("run_count", "review_count", "automatic_comparable", "blind_comparable"),
    [(5, 4, True, False), (4, 5, False, True)],
)
async def test_quality_history_applies_run_and_review_thresholds_independently(
    run_count: int,
    review_count: int,
    automatic_comparable: bool,
    blind_comparable: bool,
) -> None:
    """自动回归与盲评独立判断门槛，只隐藏证据不足的差值。"""
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    runs: list[EvaluationQualityRunSample] = []
    reviews: list[EvaluationQualityReviewSample] = []
    for index, (snapshot, pass_rate, candidate_score) in enumerate(
        (
            (_quality_snapshot(prompt_version=1), 60.0, 3.0),
            (_quality_snapshot(prompt_version=2), 85.0, 4.0),
        )
    ):
        _append_snapshot_samples(
            runs=runs,
            reviews=reviews,
            snapshot=snapshot,
            started_at=now - timedelta(days=4 - index * 2),
            run_count=run_count,
            review_count=review_count,
            pass_rate=pass_rate,
            candidate_score=candidate_score,
            reference_score=3.5,
        )

    history = await EvaluationQualityHistoryService(
        RecordingQualityHistoryReader(
            EvaluationQualitySamples(runs=tuple(runs), reviews=tuple(reviews))
        )
    ).get_history(
        tenant_id=uuid4(),
        window_minutes=10_080,
        bucket_minutes=1_440,
        now=now,
    )

    comparison = history.baseline_comparisons[0]
    assert comparison.automatic_regression_comparable is automatic_comparable
    assert comparison.blind_review_comparable is blind_comparable
    assert (comparison.pass_rate_delta_percentage_points is not None) is automatic_comparable
    assert (comparison.candidate_average_score_delta is not None) is blind_comparable
    assert (comparison.reference_average_score_delta is not None) is blind_comparable


@pytest.mark.parametrize(
    ("window_minutes", "bucket_minutes"),
    [(1_439, 60), (129_601, 1_440), (10_080, 59), (10_080, 2_000), (90_000, 900)],
)
async def test_quality_history_rejects_invalid_windows(
    window_minutes: int,
    bucket_minutes: int,
) -> None:
    """服务层独立拒绝越界、不整除或超过九十桶的查询。"""
    reader = RecordingQualityHistoryReader(EvaluationQualitySamples(runs=(), reviews=()))

    with pytest.raises(QualityHistoryValidationError):
        await EvaluationQualityHistoryService(reader).get_history(
            tenant_id=uuid4(),
            window_minutes=window_minutes,
            bucket_minutes=bucket_minutes,
        )

    assert reader.calls == []
