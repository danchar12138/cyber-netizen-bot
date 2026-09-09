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
    LatencyPercentiles,
    ModelUsageMetrics,
    ObservabilityMetrics,
    QueueMetrics,
)
from cnb_infrastructure import MemoryConfigurationRepository, MemoryObservabilityRepository


class FixedObservabilityRepository:
    """返回高于默认阈值的固定安全指标。"""

    async def record_api_request(self, observation: ApiRequestObservation) -> None:
        del observation

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> ObservabilityMetrics:
        del tenant_id
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
        "model_cost_budget",
    }
    assert all("正文" not in item.summary for item in dashboard.alerts)


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
