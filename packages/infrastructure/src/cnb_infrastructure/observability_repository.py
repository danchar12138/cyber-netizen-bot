"""可观测请求指标写入与 PostgreSQL 租户聚合。"""

import asyncio
from datetime import datetime
from math import ceil
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import ApiRequestObservation
from cnb_domain import (
    AgentRunSloMetrics,
    ApiSloMetrics,
    ChannelDeliveryMetrics,
    LatencyPercentiles,
    ModelUsageMetrics,
    NotificationDeliveryMetrics,
    ObservabilityMetrics,
    QueueMetrics,
)
from cnb_infrastructure.models import (
    AgentRunModel,
    ApiRequestMetricModel,
    BackgroundJobModel,
    ChannelDiagnosticEventModel,
    ModelInvocationModel,
)


class MemoryObservabilityRepository:
    """供测试与无数据库联调使用的请求指标仓储。"""

    def __init__(self) -> None:
        self._requests: list[ApiRequestObservation] = []
        self._lock = asyncio.Lock()

    async def record_api_request(self, observation: ApiRequestObservation) -> None:
        async with self._lock:
            self._requests.append(observation)

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> ObservabilityMetrics:
        async with self._lock:
            requests = tuple(
                item
                for item in self._requests
                if item.tenant_id == tenant_id
                and window_started_at <= item.occurred_at <= window_ended_at
            )
        server_errors = sum(item.status_code >= 500 for item in requests)
        return ObservabilityMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            api=ApiSloMetrics(
                requests=len(requests),
                server_errors=server_errors,
                error_rate_percent=_rate(server_errors, len(requests)),
                latency=_memory_percentiles(tuple(item.duration_ms for item in requests)),
            ),
            agent_runs=AgentRunSloMetrics(
                terminal_runs=0,
                completed_runs=0,
                unsuccessful_runs=0,
                success_rate_percent=100.0,
                latency=LatencyPercentiles(0, 0, 0),
            ),
            models=(),
            queue=QueueMetrics(backlog=0, oldest_wait_seconds=0),
        )


class SqlAlchemyObservabilityRepository:
    """使用数据库侧分位数与分组聚合控制读取规模。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record_api_request(self, observation: ApiRequestObservation) -> None:
        async with self._session_factory() as session, session.begin():
            session.add(
                ApiRequestMetricModel(
                    tenant_id=observation.tenant_id,
                    method=observation.method,
                    route=observation.route,
                    status_code=observation.status_code,
                    duration_ms=observation.duration_ms,
                    occurred_at=observation.occurred_at,
                )
            )

    async def get_metrics(
        self,
        *,
        tenant_id: UUID,
        window_started_at: datetime,
        window_ended_at: datetime,
    ) -> ObservabilityMetrics:
        async with self._session_factory() as session:
            api = await self._api_metrics(session, tenant_id, window_started_at, window_ended_at)
            agent_runs = await self._agent_run_metrics(
                session, tenant_id, window_started_at, window_ended_at
            )
            models = await self._model_metrics(
                session, tenant_id, window_started_at, window_ended_at
            )
            queue = await self._queue_metrics(session, tenant_id, window_ended_at)
            channel_delivery = await self._channel_delivery_metrics(
                session, tenant_id, window_started_at, window_ended_at
            )
            notification_delivery = await self._notification_delivery_metrics(
                session, tenant_id, window_started_at, window_ended_at
            )
        return ObservabilityMetrics(
            window_started_at=window_started_at,
            window_ended_at=window_ended_at,
            api=api,
            agent_runs=agent_runs,
            models=models,
            queue=queue,
            channel_delivery=channel_delivery,
            notification_delivery=notification_delivery,
        )

    @staticmethod
    async def _channel_delivery_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
    ) -> ChannelDeliveryMetrics:
        row = (
            await session.execute(
                select(
                    func.count(ChannelDiagnosticEventModel.id).filter(
                        ChannelDiagnosticEventModel.status.in_(
                            ("delivered", "degraded", "failed", "rejected", "rate_limited")
                        )
                    ),
                    func.count(ChannelDiagnosticEventModel.id).filter(
                        ChannelDiagnosticEventModel.status == "delivered"
                    ),
                    func.count(ChannelDiagnosticEventModel.id).filter(
                        ChannelDiagnosticEventModel.status == "degraded"
                    ),
                    func.count(ChannelDiagnosticEventModel.id).filter(
                        ChannelDiagnosticEventModel.status.in_(("failed", "rejected"))
                    ),
                    func.count(ChannelDiagnosticEventModel.id).filter(
                        ChannelDiagnosticEventModel.status == "rate_limited"
                    ),
                ).where(
                    ChannelDiagnosticEventModel.tenant_id == tenant_id,
                    ChannelDiagnosticEventModel.direction == "outbound",
                    ChannelDiagnosticEventModel.occurred_at >= started_at,
                    ChannelDiagnosticEventModel.occurred_at <= ended_at,
                )
            )
        ).one()
        return ChannelDeliveryMetrics(
            attempts=int(row[0] or 0),
            delivered=int(row[1] or 0),
            degraded=int(row[2] or 0),
            failed=int(row[3] or 0),
            rate_limited=int(row[4] or 0),
        )

    @staticmethod
    async def _notification_delivery_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
    ) -> NotificationDeliveryMetrics:
        row = (
            await session.execute(
                select(
                    func.count(BackgroundJobModel.id),
                    func.count(BackgroundJobModel.id).filter(
                        BackgroundJobModel.status == "pending"
                    ),
                    func.count(BackgroundJobModel.id).filter(
                        BackgroundJobModel.status == "running"
                    ),
                    func.count(BackgroundJobModel.id).filter(
                        BackgroundJobModel.status == "retrying"
                    ),
                    func.count(BackgroundJobModel.id).filter(
                        BackgroundJobModel.status == "succeeded"
                    ),
                    func.count(BackgroundJobModel.id).filter(BackgroundJobModel.status == "failed"),
                    func.count(BackgroundJobModel.id).filter(
                        BackgroundJobModel.status == "dead_letter"
                    ),
                ).where(
                    BackgroundJobModel.tenant_id == tenant_id,
                    BackgroundJobModel.kind == "notification_delivery",
                    BackgroundJobModel.created_at >= started_at,
                    BackgroundJobModel.created_at <= ended_at,
                )
            )
        ).one()
        return NotificationDeliveryMetrics(
            total=int(row[0] or 0),
            pending=int(row[1] or 0),
            running=int(row[2] or 0),
            retrying=int(row[3] or 0),
            succeeded=int(row[4] or 0),
            failed=int(row[5] or 0),
            dead_letters=int(row[6] or 0),
        )

    @staticmethod
    async def _api_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
    ) -> ApiSloMetrics:
        row = (
            await session.execute(
                select(
                    func.count(ApiRequestMetricModel.id),
                    func.count(ApiRequestMetricModel.id).filter(
                        ApiRequestMetricModel.status_code >= 500
                    ),
                    *_percentile_columns(ApiRequestMetricModel.duration_ms),
                ).where(
                    ApiRequestMetricModel.tenant_id == tenant_id,
                    ApiRequestMetricModel.occurred_at >= started_at,
                    ApiRequestMetricModel.occurred_at <= ended_at,
                )
            )
        ).one()
        requests, errors = int(row[0]), int(row[1])
        return ApiSloMetrics(
            requests=requests,
            server_errors=errors,
            error_rate_percent=_rate(errors, requests),
            latency=_row_percentiles(row, 2),
        )

    @staticmethod
    async def _agent_run_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
    ) -> AgentRunSloMetrics:
        duration_ms = (
            func.extract("epoch", AgentRunModel.completed_at - AgentRunModel.started_at) * 1000
        )
        terminal_statuses = ("completed", "failed", "cancelled")
        row = (
            await session.execute(
                select(
                    func.count(AgentRunModel.id),
                    func.count(AgentRunModel.id).filter(AgentRunModel.status == "completed"),
                    func.count(AgentRunModel.id).filter(AgentRunModel.status != "completed"),
                    *_percentile_columns(duration_ms),
                ).where(
                    AgentRunModel.tenant_id == tenant_id,
                    AgentRunModel.status.in_(terminal_statuses),
                    AgentRunModel.started_at.is_not(None),
                    AgentRunModel.completed_at >= started_at,
                    AgentRunModel.completed_at <= ended_at,
                )
            )
        ).one()
        total, completed, unsuccessful = int(row[0]), int(row[1]), int(row[2])
        return AgentRunSloMetrics(
            terminal_runs=total,
            completed_runs=completed,
            unsuccessful_runs=unsuccessful,
            success_rate_percent=100.0 if total == 0 else _rate(completed, total),
            latency=_row_percentiles(row, 3),
        )

    @staticmethod
    async def _model_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        started_at: datetime,
        ended_at: datetime,
    ) -> tuple[ModelUsageMetrics, ...]:
        rows = (
            await session.execute(
                select(
                    ModelInvocationModel.provider,
                    ModelInvocationModel.model,
                    func.count(ModelInvocationModel.id),
                    func.count(ModelInvocationModel.id).filter(
                        ModelInvocationModel.status != "completed"
                    ),
                    func.coalesce(func.sum(ModelInvocationModel.input_tokens), 0),
                    func.coalesce(func.sum(ModelInvocationModel.output_tokens), 0),
                    func.coalesce(func.sum(ModelInvocationModel.estimated_cost_microusd), 0),
                    *_percentile_columns(ModelInvocationModel.latency_ms),
                )
                .where(
                    ModelInvocationModel.tenant_id == tenant_id,
                    ModelInvocationModel.created_at >= started_at,
                    ModelInvocationModel.created_at <= ended_at,
                )
                .group_by(ModelInvocationModel.provider, ModelInvocationModel.model)
                .order_by(func.sum(ModelInvocationModel.estimated_cost_microusd).desc())
            )
        ).all()
        return tuple(
            ModelUsageMetrics(
                provider=str(row[0]),
                model=str(row[1]),
                invocations=int(row[2]),
                failed_invocations=int(row[3]),
                input_tokens=int(row[4]),
                output_tokens=int(row[5]),
                estimated_cost_microusd=int(row[6]),
                latency=_row_percentiles(row, 7),
            )
            for row in rows
        )

    @staticmethod
    async def _queue_metrics(
        session: AsyncSession,
        tenant_id: UUID,
        now: datetime,
    ) -> QueueMetrics:
        row = (
            await session.execute(
                select(
                    func.count(BackgroundJobModel.id),
                    func.min(BackgroundJobModel.available_at),
                ).where(
                    BackgroundJobModel.tenant_id == tenant_id,
                    BackgroundJobModel.status.in_(("pending", "retrying")),
                )
            )
        ).one()
        oldest = row[1]
        wait_seconds = (
            max(0, int((now - oldest).total_seconds())) if isinstance(oldest, datetime) else 0
        )
        return QueueMetrics(backlog=int(row[0]), oldest_wait_seconds=wait_seconds)


def _percentile_columns(value: Any) -> tuple[Any, Any, Any]:
    return (
        func.percentile_cont(0.5).within_group(value),
        func.percentile_cont(0.95).within_group(value),
        func.percentile_cont(0.99).within_group(value),
    )


def _row_percentiles(row: Row[Any], offset: int) -> LatencyPercentiles:
    return LatencyPercentiles(
        p50_ms=max(0, round(float(row[offset] or 0))),
        p95_ms=max(0, round(float(row[offset + 1] or 0))),
        p99_ms=max(0, round(float(row[offset + 2] or 0))),
    )


def _memory_percentiles(values: tuple[int, ...]) -> LatencyPercentiles:
    if not values:
        return LatencyPercentiles(0, 0, 0)
    ordered = sorted(values)

    def percentile(value: float) -> int:
        return ordered[max(0, ceil(len(ordered) * value) - 1)]

    return LatencyPercentiles(percentile(0.5), percentile(0.95), percentile(0.99))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator * 100 / denominator, 4) if denominator else 0.0
