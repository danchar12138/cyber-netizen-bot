"""可观测聚合与告警领域边界测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    ApiRequestObservation,
    ConfigurationService,
    EntityCursor,
    ObservabilityNotFoundError,
    ObservabilityService,
    ObservabilityValidationError,
    build_default_registry,
)
from cnb_domain import (
    ActiveAlert,
    AgentRunSloMetrics,
    AlertSeverity,
    ApiSloMetrics,
    ChannelDeliveryMetrics,
    LatencyPercentiles,
    ModelUsageMetrics,
    NotificationDeliveryMetrics,
    ObservabilityAlertDisposition,
    ObservabilityAlertDispositionAction,
    ObservabilityAlertDispositionStatus,
    ObservabilityAlertLifecycle,
    ObservabilityAlertLifecycleStatus,
    ObservabilityAlertRecommendationAction,
    ObservabilityAlertRecommendationPriority,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayReason,
    ObservabilityAlertReplayReview,
    ObservabilityMetrics,
    QueueMetrics,
)
from cnb_infrastructure import MemoryConfigurationRepository, MemoryObservabilityRepository


class FixedObservabilityRepository(MemoryObservabilityRepository):
    """返回高于默认阈值的固定安全指标。"""

    def __init__(self) -> None:
        super().__init__()
        self.alerting = True
        self.requested_agent_id: UUID | None = None

    async def record_api_request(self, observation: ApiRequestObservation) -> None:
        del observation

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
        agent_id: UUID | None = None,
    ) -> ObservabilityMetrics:
        del tenant_id
        self.requested_agent_id = agent_id
        if not self.alerting:
            return ObservabilityMetrics(
                window_started_at=window_started_at,
                window_ended_at=window_ended_at,
                api=ApiSloMetrics(
                    requests=0,
                    server_errors=0,
                    error_rate_percent=0,
                    latency=LatencyPercentiles(0, 0, 0),
                ),
                agent_runs=AgentRunSloMetrics(
                    terminal_runs=0,
                    completed_runs=0,
                    unsuccessful_runs=0,
                    success_rate_percent=100,
                    latency=LatencyPercentiles(0, 0, 0),
                ),
                models=(),
                queue=QueueMetrics(backlog=0, oldest_wait_seconds=0),
            )
        return ObservabilityMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            api=ApiSloMetrics(
                requests=10,
                server_errors=2,
                error_rate_percent=20.0,
                latency=LatencyPercentiles(100, 900, 1200),
            ),
            agent_runs=AgentRunSloMetrics(
                terminal_runs=10,
                completed_runs=8,
                unsuccessful_runs=2,
                success_rate_percent=80.0,
                latency=LatencyPercentiles(5000, 20000, 25000),
            ),
            models=(
                ModelUsageMetrics(
                    provider="provider",
                    model="model",
                    invocations=10,
                    failed_invocations=2,
                    input_tokens=1000,
                    output_tokens=500,
                    estimated_cost_microusd=6_000_000,
                    latency=LatencyPercentiles(100, 900, 1200),
                ),
            ),
            queue=QueueMetrics(backlog=101, oldest_wait_seconds=301),
            channel_delivery=ChannelDeliveryMetrics(
                attempts=10,
                delivered=7,
                degraded=1,
                failed=1,
                rate_limited=1,
            ),
            notification_delivery=NotificationDeliveryMetrics(
                total=4,
                pending=0,
                running=0,
                retrying=1,
                succeeded=2,
                failed=0,
                dead_letters=1,
            ),
        )


async def test_dashboard_calculates_all_default_threshold_alerts() -> None:
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    service = ObservabilityService(
        FixedObservabilityRepository(),
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )

    dashboard = await service.dashboard(tenant_id=uuid4(), now=now)

    assert dashboard.metrics.window_started_at == now - timedelta(minutes=60)
    assert dashboard.total_estimated_cost_microusd == 6_000_000
    assert {item.code for item in dashboard.alerts} == {
        "api_error_rate",
        "api_p95_latency",
        "agent_success_rate",
        "agent_p95_latency",
        "queue_backlog",
        "model_failure_rate",
        "queue_oldest_wait",
        "channel_delivery_failure_rate",
        "notification_delivery_dead_letters",
        "model_cost_budget",
    }
    assert all("正文" not in item.summary for item in dashboard.alerts)


async def test_dashboard_uses_selected_agent_scope_for_all_metrics() -> None:
    repository = FixedObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    agent_id = uuid4()

    await service.dashboard(tenant_id=uuid4(), agent_id=agent_id)

    assert repository.requested_agent_id == agent_id


async def test_all_non_channel_sources_share_lifecycle_and_recovery_rules() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    repository = FixedObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)

    first = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=started_at,
    )
    second = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=started_at + timedelta(minutes=1),
    )

    expected_sources = {
        "api_error_rate": "api",
        "api_p95_latency": "api",
        "agent_success_rate": "agent_runtime",
        "agent_p95_latency": "agent_runtime",
        "model_failure_rate": "model_runtime",
        "model_cost_budget": "model_runtime",
        "queue_backlog": "task_queue",
        "queue_oldest_wait": "task_queue",
        "notification_delivery_dead_letters": "notification",
    }
    assert {item.code: item.source_type for item in first.active_lifecycles} == expected_sources
    assert "channel_delivery_failure_rate" not in {item.code for item in first.active_lifecycles}
    assert {item.id for item in second.active_lifecycles} == {
        item.id for item in first.active_lifecycles
    }
    assert all(item.occurrences == 2 for item in second.active_lifecycles)
    assert repository.requested_agent_id == agent_id

    escalated = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=started_at + timedelta(minutes=31),
    )
    assert {item.lifecycle.code for item in escalated.due_escalations} == set(expected_sources)

    repository.alerting = False
    recovered_at = started_at + timedelta(minutes=32)
    recovered = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=recovered_at,
    )
    resolved = await service.alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=ObservabilityAlertLifecycleStatus.RESOLVED,
    )

    assert recovered.active_lifecycles == ()
    assert {item.code for item in resolved} == set(expected_sources)
    assert all(item.resolved_at == recovered_at for item in resolved)


async def test_memory_metrics_are_tenant_isolated_and_use_route_templates() -> None:
    repository = MemoryObservabilityRepository()
    tenant_id = uuid4()
    now = datetime.now(UTC)
    await repository.record_api_request(
        ApiRequestObservation(tenant_id, "GET", "/api/v1/chat/conversations", 200, 11, now)
    )
    await repository.record_api_request(
        ApiRequestObservation(uuid4(), "POST", "/foreign", 500, 999, now)
    )

    metrics = await repository.get_metrics(
        tenant_id=tenant_id,
        window_started_at=now - timedelta(minutes=1),
        window_ended_at=now + timedelta(minutes=1),
    )

    assert metrics.api.requests == 1
    assert metrics.api.server_errors == 0
    assert metrics.api.latency == LatencyPercentiles(11, 11, 11)


async def test_channel_delivery_failure_rate_includes_rate_limits() -> None:
    metrics = ChannelDeliveryMetrics(attempts=8, delivered=5, failed=1, rate_limited=2)

    assert metrics.failure_rate_percent == 37.5


def _api_error_observation(
    tenant_id: UUID, agent_id: UUID, occurred_at: datetime
) -> ApiRequestObservation:
    return ApiRequestObservation(
        tenant_id,
        "GET",
        "/api/v1/chat/conversations",
        500,
        25,
        occurred_at,
        agent_id,
    )


async def test_observability_lifecycle_reuses_active_event_and_counts_evaluations() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    configuration = ConfigurationService(build_default_registry(), MemoryConfigurationRepository())
    service = ObservabilityService(repository, configuration)
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    await repository.record_api_request(_api_error_observation(tenant_id, agent_id, now))

    first = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=now
    )
    second = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=now
    )

    assert len(first.active_lifecycles) == 1
    assert first.activated_lifecycles == first.active_lifecycles
    assert first.recovered_lifecycles == ()
    assert first.active_lifecycles[0].status is ObservabilityAlertLifecycleStatus.ACTIVE
    assert second.activated_lifecycles == ()
    assert second.recovered_lifecycles == ()
    assert second.active_lifecycles[0].id == first.active_lifecycles[0].id
    assert second.active_lifecycles[0].occurrences == 2
    assert second.active_lifecycles[0].first_occurred_at == now


async def test_observability_lifecycle_resolves_after_window_recovers() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    await repository.record_api_request(_api_error_observation(tenant_id, agent_id, started_at))
    await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=started_at + timedelta(minutes=1)
    )

    recovered_at = started_at + timedelta(minutes=61)
    evaluation = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=recovered_at
    )
    rows = await service.alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=ObservabilityAlertLifecycleStatus.RESOLVED,
    )

    assert evaluation.active_lifecycles == ()
    assert evaluation.activated_lifecycles == ()
    assert len(evaluation.recovered_lifecycles) == 1
    assert evaluation.recovered_lifecycles[0].resolved_at == recovered_at
    assert len(rows) == 1
    assert rows[0].status is ObservabilityAlertLifecycleStatus.RESOLVED
    assert rows[0].resolved_at == recovered_at
    assert rows[0].recovery_duration_seconds == 60 * 60


async def test_observability_lifecycle_escalation_is_isolated_by_tenant_and_agent() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    other_tenant, other_agent = uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    await repository.record_api_request(_api_error_observation(tenant_id, agent_id, started_at))
    await repository.record_api_request(
        _api_error_observation(other_tenant, other_agent, started_at)
    )
    await service.reconcile_alert_lifecycles(tenant_id=tenant_id, agent_id=agent_id, now=started_at)
    await service.reconcile_alert_lifecycles(
        tenant_id=other_tenant, agent_id=other_agent, now=started_at
    )

    evaluation = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=started_at + timedelta(minutes=31)
    )
    assert len(evaluation.due_escalations) == 1
    lifecycle = evaluation.active_lifecycles[0]
    assert lifecycle.tenant_id == tenant_id
    assert lifecycle.agent_id == agent_id
    marked = await service.mark_alert_lifecycles_escalated(
        tenant_id=other_tenant,
        agent_id=other_agent,
        lifecycle_ids=(lifecycle.id,),
        escalation_level=1,
        escalated_at=started_at + timedelta(minutes=31),
    )
    assert marked == ()


async def test_unattributed_api_observations_are_excluded_from_agent_scan() -> None:
    repository = MemoryObservabilityRepository()
    tenant_id = uuid4()
    now = datetime(2026, 9, 14, 12, tzinfo=UTC)
    await repository.record_api_request(
        ApiRequestObservation(tenant_id, "GET", "/health", 500, 20, now, None)
    )

    assert await repository.list_observability_agent_ids() == ()


async def test_observability_alert_filters_and_dispositions_form_a_scoped_lifecycle() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    reconciliation = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(
            ActiveAlert(
                code="api_error_rate",
                severity=AlertSeverity.CRITICAL,
                title="API 错误率",
                summary="安全摘要",
                current_value=20,
                threshold_value=1,
                unit="%",
                source_type="api",
                source_key="api_error_rate",
                first_occurred_at=started_at - timedelta(minutes=40),
            ),
            ActiveAlert(
                code="queue_backlog",
                severity=AlertSeverity.WARNING,
                title="任务积压",
                summary="安全摘要",
                current_value=101,
                threshold_value=100,
                unit="jobs",
                source_type="task_queue",
                source_key="queue_backlog",
                first_occurred_at=started_at - timedelta(minutes=5),
            ),
        ),
        observed_at=started_at,
    )
    target = next(item for item in reconciliation.active_lifecycles if item.source_type == "api")

    with pytest.raises(ObservabilityValidationError, match="明确确认"):
        await service.acknowledge_alert(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_id=target.id,
            reason="已交由值班人员处理",
            actor_id=actor_id,
            confirmed=False,
            now=started_at,
        )

    acknowledged = await service.acknowledge_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=target.id,
        reason="  已交由值班人员处理  ",
        actor_id=actor_id,
        confirmed=True,
        now=started_at,
    )
    filtered = await service.alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=ObservabilityAlertLifecycleStatus.ACTIVE,
        source_type="api",
        severity=AlertSeverity.CRITICAL,
        minimum_duration_minutes=30,
        now=started_at,
    )

    assert len(filtered) == 1
    assert filtered[0].id == target.id
    assert filtered[0].disposition_status is ObservabilityAlertDispositionStatus.ACKNOWLEDGED
    assert filtered[0].disposition_reason == "已交由值班人员处理"

    suppressed = await service.suppress_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=target.id,
        reason="维护窗口",
        expires_at=started_at + timedelta(hours=1),
        actor_id=actor_id,
        confirmed=True,
        now=started_at,
    )
    assert suppressed.id == acknowledged.id
    assert suppressed.created_at == acknowledged.created_at

    before_expiry = await service.alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=started_at + timedelta(minutes=59)
    )
    after_expiry = await service.alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=started_at + timedelta(hours=1)
    )
    before_target = next(item for item in before_expiry if item.id == target.id)
    after_target = next(item for item in after_expiry if item.id == target.id)
    assert before_target.disposition_status is ObservabilityAlertDispositionStatus.SUPPRESSED
    assert after_target.disposition_status is None

    with pytest.raises(ObservabilityNotFoundError, match="生命周期不存在"):
        await service.clear_alert_disposition(
            tenant_id=tenant_id,
            agent_id=uuid4(),
            lifecycle_id=target.id,
            actor_id=actor_id,
            confirmed=True,
        )

    removed = await service.clear_alert_disposition(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=target.id,
        actor_id=actor_id,
        confirmed=True,
    )
    assert removed.status is ObservabilityAlertDispositionStatus.SUPPRESSED
    assert (
        await repository.list_observability_alert_dispositions(
            tenant_id=tenant_id, agent_id=agent_id
        )
        == ()
    )


async def test_suppressed_observability_alert_is_excluded_from_escalation_candidates() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    repository = FixedObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    started_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    first = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id, agent_id=agent_id, now=started_at
    )
    target = next(item for item in first.active_lifecycles if item.code == "api_error_rate")
    await service.suppress_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=target.id,
        reason="调查中",
        expires_at=started_at + timedelta(hours=2),
        actor_id=uuid4(),
        confirmed=True,
        now=started_at,
    )

    evaluation = await service.reconcile_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=started_at + timedelta(minutes=31),
    )

    assert "api_error_rate" not in {item.lifecycle.code for item in evaluation.due_escalations}
    enriched = next(item for item in evaluation.active_lifecycles if item.id == target.id)
    assert enriched.disposition_status is ObservabilityAlertDispositionStatus.SUPPRESSED


async def test_batch_disposition_appends_history_and_is_agent_scoped() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    reconciliation = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(
            ActiveAlert(
                code="api_error_rate",
                severity=AlertSeverity.CRITICAL,
                title="API 错误率",
                summary="安全摘要",
                current_value=20,
                threshold_value=1,
                unit="%",
                source_type="api",
                source_key="api_error_rate",
            ),
            ActiveAlert(
                code="queue_backlog",
                severity=AlertSeverity.WARNING,
                title="任务积压",
                summary="安全摘要",
                current_value=101,
                threshold_value=100,
                unit="jobs",
                source_type="task_queue",
                source_key="queue_backlog",
            ),
        ),
        observed_at=now,
    )
    ids = tuple(item.id for item in reconciliation.active_lifecycles)
    acknowledged = await service.batch_disposition(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_ids=ids,
        action="acknowledge",
        reason="批量交由值班人员处理",
        expires_at=None,
        actor_id=actor_id,
        confirmed=True,
        now=now,
    )
    assert {item.disposition.status for item in acknowledged} == {
        ObservabilityAlertDispositionStatus.ACKNOWLEDGED
    }
    history = await service.disposition_events(
        tenant_id=tenant_id,
        agent_id=agent_id,
        action=ObservabilityAlertDispositionAction.ACKNOWLEDGED,
    )
    assert len(history) == 2
    assert {item.lifecycle_id for item in history} == set(ids)

    with pytest.raises(ObservabilityValidationError, match="不能重复"):
        await service.batch_disposition(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_ids=(ids[0], ids[0]),
            action="acknowledge",
            reason="重复目标",
            expires_at=None,
            actor_id=actor_id,
            confirmed=True,
        )

    cleared = await service.batch_disposition(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_ids=ids,
        action="clear",
        reason="批量解除处置",
        expires_at=None,
        actor_id=actor_id,
        confirmed=True,
    )
    assert len(cleared) == 2
    all_history = await service.disposition_events(tenant_id=tenant_id, agent_id=agent_id)
    assert [item.action for item in all_history].count(
        ObservabilityAlertDispositionAction.CLEARED
    ) == 2
    assert await service.disposition_events(tenant_id=uuid4(), agent_id=agent_id) == ()


async def test_observability_lifecycle_metrics_use_independent_source_and_trend_scope() -> None:
    """通用指标按来源聚合事件，不读取渠道生命周期口径。"""
    tenant_id, agent_id = uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    ended_at = datetime(2026, 9, 15, 12, tzinfo=UTC)

    def alert(
        code: str,
        source_type: str,
        severity: AlertSeverity,
        first_occurred_at: datetime,
    ) -> ActiveAlert:
        return ActiveAlert(
            code=code,
            severity=severity,
            title="安全测试告警",
            summary="不含正文的安全摘要",
            current_value=2,
            threshold_value=1,
            unit="count",
            source_type=source_type,
            source_key=code,
            first_occurred_at=first_occurred_at,
        )

    api_alert = alert(
        "api_error_rate", "api", AlertSeverity.CRITICAL, ended_at - timedelta(hours=4, minutes=30)
    )
    model_alert = alert(
        "model_failure_rate",
        "model_runtime",
        AlertSeverity.CRITICAL,
        ended_at - timedelta(hours=3, minutes=30),
    )
    queue_alert = alert(
        "queue_backlog", "task_queue", AlertSeverity.WARNING, ended_at - timedelta(minutes=45)
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(api_alert,),
        observed_at=ended_at - timedelta(hours=4, minutes=30),
    )
    active = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(api_alert, model_alert),
        observed_at=ended_at - timedelta(hours=3, minutes=30),
    )
    model_lifecycle = next(
        item for item in active.active_lifecycles if item.source_type == "model_runtime"
    )
    await repository.mark_observability_alert_lifecycles_escalated(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_ids=(model_lifecycle.id,),
        escalation_level=1,
        escalated_at=ended_at - timedelta(hours=2, minutes=50),
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(api_alert,),
        observed_at=ended_at - timedelta(hours=2),
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(api_alert, queue_alert),
        observed_at=ended_at - timedelta(minutes=45),
    )

    metrics = await service.alert_lifecycle_metrics(
        tenant_id=tenant_id,
        agent_id=agent_id,
        window_minutes=240,
        bucket_minutes=60,
        now=ended_at,
    )
    model_metrics = await service.alert_lifecycle_metrics(
        tenant_id=tenant_id,
        agent_id=agent_id,
        window_minutes=240,
        bucket_minutes=60,
        source_type=" model_runtime ",
        severity=AlertSeverity.CRITICAL,
        now=ended_at,
    )

    assert (metrics.active, metrics.opened, metrics.resolved, metrics.escalated) == (2, 2, 1, 1)
    assert metrics.mean_recovery_seconds == 5_400
    assert metrics.p95_recovery_seconds == 5_400
    assert [item.source_type for item in metrics.sources] == ["api", "model_runtime", "task_queue"]
    assert [(item.opened, item.resolved, item.escalated) for item in metrics.sources] == [
        (0, 0, 0),
        (1, 1, 1),
        (1, 0, 0),
    ]
    assert [(item.opened, item.resolved, item.escalated) for item in metrics.trend] == [
        (1, 0, 0),
        (0, 0, 1),
        (0, 1, 0),
        (1, 0, 0),
    ]
    assert len(model_metrics.sources) == 1
    assert model_metrics.sources[0].source_type == "model_runtime"
    assert (model_metrics.active, model_metrics.opened, model_metrics.resolved) == (0, 1, 1)

    with pytest.raises(ObservabilityValidationError, match="时间桶"):
        await service.alert_lifecycle_metrics(
            tenant_id=tenant_id,
            agent_id=agent_id,
            window_minutes=60,
            bucket_minutes=120,
        )


async def test_alert_operations_summary_uses_robust_baseline_and_safe_handoff() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    ended_at = datetime(2026, 9, 15, 12, tzinfo=UTC)
    window = timedelta(minutes=480)
    current_started_at = ended_at - window
    history_started_at = current_started_at - window * 7

    def alert(
        *,
        source_key: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        occurred_at: datetime,
    ) -> ActiveAlert:
        return ActiveAlert(
            code="api_error_rate",
            severity=severity,
            title="API 错误率",
            summary="安全摘要",
            current_value=20,
            threshold_value=1,
            unit="%",
            source_type="api",
            source_key=source_key,
            first_occurred_at=occurred_at,
        )

    for sample_index, count in enumerate((1, 1, 20, 1, 1, 1, 1)):
        occurred_at = history_started_at + window * sample_index + timedelta(minutes=10)
        await repository.reconcile_observability_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            alerts=tuple(
                alert(
                    source_key=f"historical-{sample_index}-{item_index}",
                    occurred_at=occurred_at,
                )
                for item_index in range(count)
            ),
            observed_at=occurred_at,
        )
        await repository.reconcile_observability_alert_lifecycles(
            tenant_id=tenant_id,
            agent_id=agent_id,
            alerts=(),
            observed_at=occurred_at + timedelta(minutes=1),
        )

    active_at = ended_at - timedelta(minutes=10)
    current = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(
            alert(source_key="acknowledged", occurred_at=active_at),
            alert(source_key="suppressed", occurred_at=active_at),
            alert(
                source_key="expired-suppression",
                severity=AlertSeverity.CRITICAL,
                occurred_at=active_at,
            ),
            alert(
                source_key="escalated",
                severity=AlertSeverity.CRITICAL,
                occurred_at=active_at,
            ),
        ),
        observed_at=active_at,
    )
    by_key = {item.source_key: item for item in current.active_lifecycles}
    await service.acknowledge_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=by_key["acknowledged"].id,
        reason="内部处置备注不得进入交接摘要",
        actor_id=actor_id,
        confirmed=True,
        now=active_at,
    )
    await service.suppress_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=by_key["suppressed"].id,
        reason="近期维护窗口",
        expires_at=ended_at + timedelta(hours=1),
        actor_id=actor_id,
        confirmed=True,
        now=active_at,
    )
    await service.suppress_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=by_key["expired-suppression"].id,
        reason="已过期的维护窗口",
        expires_at=ended_at - timedelta(minutes=1),
        actor_id=actor_id,
        confirmed=True,
        now=active_at,
    )
    await repository.mark_observability_alert_lifecycles_escalated(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_ids=(by_key["escalated"].id,),
        escalation_level=2,
        escalated_at=ended_at - timedelta(minutes=5),
    )
    await repository.record_observability_alert_replay_review(
        ObservabilityAlertReplayReview(
            id=uuid4(),
            tenant_id=tenant_id,
            agent_id=agent_id,
            source_job_id=uuid4(),
            source_type="api",
            source_key="suppressed",
            decision=ObservabilityAlertReplayDecision.BLOCKED,
            reason_code=ObservabilityAlertReplayReason.BLOCKED_ACTIVE_SUPPRESSION,
            actor_id=actor_id,
            suppression_expires_at=ended_at + timedelta(hours=1),
            reviewed_at=ended_at - timedelta(minutes=2),
        )
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=uuid4(),
        agent_id=agent_id,
        alerts=tuple(
            alert(source_key=f"foreign-{index}", occurred_at=active_at) for index in range(10)
        ),
        observed_at=active_at,
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=uuid4(),
        alerts=tuple(
            alert(source_key=f"other-agent-{index}", occurred_at=active_at) for index in range(10)
        ),
        observed_at=active_at,
    )

    summary = await service.alert_operations_summary(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=ended_at,
    )

    opened = next(
        item
        for item in summary.baseline.signals
        if item.metric == "opened" and item.source_type is None
    )
    assert opened.samples == (1, 1, 20, 1, 1, 1, 1)
    assert opened.current_value == 4
    assert opened.baseline_median == 1
    assert opened.baseline_mad == 0
    assert opened.threshold_value == 4
    assert opened.anomalous is True
    assert any(
        item.metric == "opened" and item.source_type == "api" and item.anomalous
        for item in summary.baseline.signals
    )
    assert (
        summary.handoff.active,
        summary.handoff.critical_active,
        summary.handoff.unacknowledged_active,
        summary.handoff.acknowledged_active,
        summary.handoff.suppressed_active,
    ) == (4, 2, 2, 1, 1)
    assert (summary.handoff.opened, summary.handoff.escalated) == (4, 1)
    assert summary.handoff.blocked_replays == 1
    assert summary.handoff.priority_items[0].source_key == "escalated"
    assert summary.handoff.priority_items[0].reason_codes == (
        "critical",
        "unacknowledged",
        "escalated",
    )
    suppressed_item = next(
        item for item in summary.handoff.priority_items if item.source_key == "suppressed"
    )
    assert suppressed_item.reason_codes == ("suppression_expiring",)
    assert not hasattr(suppressed_item, "disposition_reason")
    assert summary.handoff.sources[0].source_type == "api"
    assert summary.handoff.sources[0].active == 4


async def test_alert_recommendations_are_deterministic_scoped_and_never_execute() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)

    def alert(source_type: str, source_key: str, severity: AlertSeverity) -> ActiveAlert:
        return ActiveAlert(
            code=f"{source_type}_test",
            severity=severity,
            title="建议规则测试",
            summary="不得进入建议响应的安全摘要",
            current_value=20,
            threshold_value=10,
            unit="count",
            source_type=source_type,
            source_key=source_key,
            first_occurred_at=now - timedelta(minutes=30),
            last_occurred_at=now,
        )

    critical = alert("agent_runtime", "critical", AlertSeverity.CRITICAL)
    repeated = alert("task_queue", "repeated", AlertSeverity.WARNING)
    observed = alert("model_runtime", "observed", AlertSeverity.WARNING)
    acknowledged = alert("notification", "acknowledged", AlertSeverity.WARNING)
    all_alerts = (critical, repeated, observed, acknowledged)
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=all_alerts,
        observed_at=now - timedelta(minutes=3),
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=all_alerts,
        observed_at=now - timedelta(minutes=2),
    )
    current = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=(critical, repeated, acknowledged),
        observed_at=now - timedelta(minutes=1),
    )
    current = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=all_alerts,
        observed_at=now,
    )
    by_key = {item.source_key: item for item in current.active_lifecycles}
    await repository.mark_observability_alert_lifecycles_escalated(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_ids=(by_key["critical"].id,),
        escalation_level=1,
        escalated_at=now,
    )
    await service.acknowledge_alert(
        tenant_id=tenant_id,
        agent_id=agent_id,
        lifecycle_id=by_key["acknowledged"].id,
        reason="已由值班人员接手",
        actor_id=actor_id,
        confirmed=True,
        now=now,
    )

    recommendations = await service.alert_recommendations(
        tenant_id=tenant_id,
        agent_id=agent_id,
        now=now,
    )
    repeated_only = await service.alert_recommendations(
        tenant_id=tenant_id,
        agent_id=agent_id,
        action=ObservabilityAlertRecommendationAction.SUPPRESS,
        source_type=" task_queue ",
        now=now,
    )
    other_agent = await service.alert_recommendations(
        tenant_id=tenant_id,
        agent_id=uuid4(),
        now=now,
    )

    assert [item.source_key for item in recommendations] == [
        "critical",
        "repeated",
        "observed",
    ]
    urgent, suppression, observation = recommendations
    assert urgent.action is ObservabilityAlertRecommendationAction.ACKNOWLEDGE
    assert urgent.priority is ObservabilityAlertRecommendationPriority.URGENT
    assert urgent.reason_codes == ("critical", "escalated")
    assert suppression.action is ObservabilityAlertRecommendationAction.SUPPRESS
    assert suppression.suggested_suppression_minutes == 60
    assert suppression.reason_codes == ("repeated_warning",)
    assert observation.action is ObservabilityAlertRecommendationAction.OBSERVE
    assert observation.reason_codes == ("insufficient_signal",)
    assert all(item.requires_confirmation for item in recommendations)
    assert all(not item.automation_allowed for item in recommendations)
    assert all(len(item.guardrail_codes) == 3 for item in recommendations)
    assert repeated_only == (suppression,)
    assert other_agent == ()


async def test_alert_recommendations_reject_an_unbounded_active_scan() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    lifecycle = ObservabilityAlertLifecycle(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="api",
        source_key="overflow",
        code="api_error_rate",
        status=ObservabilityAlertLifecycleStatus.ACTIVE,
        severity=AlertSeverity.WARNING,
        occurrences=1,
        current_value=20,
        threshold_value=1,
        unit="%",
        first_occurred_at=now - timedelta(minutes=1),
        last_occurred_at=now,
        last_evaluated_at=now,
        escalated_at=None,
        resolved_at=None,
        recovery_duration_seconds=None,
        created_at=now - timedelta(minutes=1),
        updated_at=now,
    )

    class OverflowRepository(MemoryObservabilityRepository):
        requested_limit: int | None = None

        async def list_observability_alert_lifecycles(
            self,
            *,
            tenant_id: UUID,
            agent_id: UUID,
            status: ObservabilityAlertLifecycleStatus | None = None,
            source_type: str | None = None,
            severity: AlertSeverity | None = None,
            minimum_duration_minutes: int | None = None,
            evaluated_at: datetime | None = None,
            cursor: EntityCursor | None = None,
            limit: int = 100,
        ) -> tuple[ObservabilityAlertLifecycle, ...]:
            del (
                tenant_id,
                agent_id,
                status,
                source_type,
                severity,
                minimum_duration_minutes,
                evaluated_at,
                cursor,
            )
            self.requested_limit = limit
            return (lifecycle,) * limit

    repository = OverflowRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )

    with pytest.raises(ObservabilityValidationError, match="500 条活动生命周期安全上限"):
        await service.alert_recommendations(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=now,
        )

    assert repository.requested_limit == 501


async def test_alert_operations_summary_rejects_unbounded_history() -> None:
    tenant_id, agent_id = uuid4(), uuid4()
    ended_at = datetime(2026, 9, 15, 12, tzinfo=UTC)
    lifecycle = ObservabilityAlertLifecycle(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="api",
        source_key="overflow",
        code="api_error_rate",
        status=ObservabilityAlertLifecycleStatus.RESOLVED,
        severity=AlertSeverity.WARNING,
        occurrences=1,
        current_value=20,
        threshold_value=1,
        unit="%",
        first_occurred_at=ended_at - timedelta(minutes=1),
        last_occurred_at=ended_at - timedelta(minutes=1),
        last_evaluated_at=ended_at,
        escalated_at=None,
        resolved_at=ended_at,
        recovery_duration_seconds=60,
        created_at=ended_at - timedelta(minutes=1),
        updated_at=ended_at,
    )

    class OverflowRepository(MemoryObservabilityRepository):
        requested_limit: int | None = None

        async def list_observability_alert_lifecycles_in_window(
            self,
            *,
            tenant_id: UUID,
            agent_id: UUID,
            window_started_at: datetime,
            window_ended_at: datetime,
            source_type: str | None = None,
            severity: AlertSeverity | None = None,
            limit: int | None = None,
        ) -> tuple[ObservabilityAlertLifecycle, ...]:
            del tenant_id, agent_id, window_started_at, window_ended_at, source_type, severity
            self.requested_limit = limit
            return (lifecycle,) * (limit or 0)

    repository = OverflowRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )

    with pytest.raises(ObservabilityValidationError, match="10000 条生命周期安全上限"):
        await service.alert_operations_summary(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=ended_at,
        )

    assert repository.requested_limit == 10_001


async def test_alert_operations_summary_rejects_unbounded_dispositions() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    ended_at = datetime(2026, 9, 15, 12, tzinfo=UTC)
    disposition = ObservabilityAlertDisposition(
        id=uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="api",
        source_key="overflow",
        code="api_error_rate",
        status=ObservabilityAlertDispositionStatus.ACKNOWLEDGED,
        reason="不得出现在摘要中",
        actor_id=actor_id,
        expires_at=None,
        created_at=ended_at,
        updated_at=ended_at,
    )

    class OverflowDispositionRepository(MemoryObservabilityRepository):
        requested_limit: int | None = None

        async def list_observability_alert_dispositions(
            self,
            *,
            tenant_id: UUID,
            agent_id: UUID,
            limit: int | None = None,
        ) -> tuple[ObservabilityAlertDisposition, ...]:
            del tenant_id, agent_id
            self.requested_limit = limit
            return (disposition,) * (limit or 0)

    repository = OverflowDispositionRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )

    with pytest.raises(ObservabilityValidationError, match="10000 条处置记录安全上限"):
        await service.alert_operations_summary(
            tenant_id=tenant_id,
            agent_id=agent_id,
            now=ended_at,
        )

    assert repository.requested_limit == 10_001


async def test_lifecycle_and_replay_review_pages_use_stable_scoped_cursors() -> None:
    tenant_id, agent_id, actor_id = uuid4(), uuid4(), uuid4()
    repository = MemoryObservabilityRepository()
    service = ObservabilityService(
        repository,
        ConfigurationService(build_default_registry(), MemoryConfigurationRepository()),
    )
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    alerts = tuple(
        ActiveAlert(
            code=f"api_test_{index}",
            severity=AlertSeverity.WARNING,
            title="分页测试",
            summary="安全摘要",
            current_value=index + 1,
            threshold_value=1,
            unit="count",
            source_type="api",
            source_key=f"api_test_{index}",
        )
        for index in range(3)
    )
    await repository.reconcile_observability_alert_lifecycles(
        tenant_id=tenant_id,
        agent_id=agent_id,
        alerts=alerts,
        observed_at=now,
    )

    first = await service.alert_lifecycle_page(
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type=" api ",
        limit=2,
        now=now,
    )
    second = await service.alert_lifecycle_page(
        tenant_id=tenant_id,
        agent_id=agent_id,
        source_type="api",
        cursor=first.next_cursor,
        limit=2,
        now=now,
    )

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert len(second.items) == 1
    assert second.next_cursor is None
    assert {item.id for item in first.items}.isdisjoint(item.id for item in second.items)
    assert await service.alert_lifecycle_page(
        tenant_id=tenant_id,
        agent_id=uuid4(),
        limit=2,
        now=now,
    ) == type(first)(items=(), next_cursor=None)
    with pytest.raises(ObservabilityValidationError, match="分页游标无效"):
        await service.alert_lifecycle_page(
            tenant_id=tenant_id,
            agent_id=agent_id,
            cursor="not-a-cursor",
        )

    for index, (decision, reason) in enumerate(
        (
            (
                ObservabilityAlertReplayDecision.ALLOWED,
                ObservabilityAlertReplayReason.ALLOWED_NO_SUPPRESSION,
            ),
            (
                ObservabilityAlertReplayDecision.BLOCKED,
                ObservabilityAlertReplayReason.BLOCKED_ACTIVE_SUPPRESSION,
            ),
            (
                ObservabilityAlertReplayDecision.ALLOWED,
                ObservabilityAlertReplayReason.ALLOWED_SUPPRESSION_EXPIRED,
            ),
        )
    ):
        await repository.record_observability_alert_replay_review(
            ObservabilityAlertReplayReview(
                id=uuid4(),
                tenant_id=tenant_id,
                agent_id=agent_id,
                source_job_id=uuid4(),
                source_type="api",
                source_key=f"api_test_{index}",
                decision=decision,
                reason_code=reason,
                actor_id=actor_id,
                suppression_expires_at=None,
                reviewed_at=now,
            )
        )
    review_first = await service.alert_replay_reviews(
        tenant_id=tenant_id, agent_id=agent_id, limit=2
    )
    review_second = await service.alert_replay_reviews(
        tenant_id=tenant_id,
        agent_id=agent_id,
        cursor=review_first.next_cursor,
        limit=2,
    )
    metrics = await service.alert_replay_metrics(
        tenant_id=tenant_id,
        agent_id=agent_id,
        window_minutes=60,
        now=now + timedelta(minutes=1),
    )

    assert len(review_first.items) == 2
    assert len(review_second.items) == 1
    assert {item.id for item in review_first.items}.isdisjoint(
        item.id for item in review_second.items
    )
    assert (metrics.total, metrics.allowed, metrics.blocked) == (3, 2, 1)
    assert metrics.allowed_rate_percent == pytest.approx(66.6667)
    assert sum(item.count for item in metrics.reasons) == metrics.total
    assert [(item.source_type, item.total) for item in metrics.sources] == [("api", 3)]
