"""可观测聚合与告警领域边界测试。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from cnb_application import (
    ApiRequestObservation,
    ConfigurationService,
    ObservabilityService,
    build_default_registry,
)
from cnb_domain import (
    AgentRunSloMetrics,
    ApiSloMetrics,
    ChannelDeliveryMetrics,
    LatencyPercentiles,
    ModelUsageMetrics,
    NotificationDeliveryMetrics,
    ObservabilityAlertLifecycleStatus,
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
