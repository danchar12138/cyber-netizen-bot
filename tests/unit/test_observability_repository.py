"""PostgreSQL 可观测聚合的 Agent 隔离查询测试。"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from cnb_infrastructure import SqlAlchemyObservabilityRepository


class _CapturedResult:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def one(self) -> tuple[object, ...]:
        return self._row

    def all(self) -> list[tuple[object, ...]]:
        return []


class _CapturingSession:
    def __init__(self) -> None:
        self.row: tuple[object, ...] = ()
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _CapturedResult:
        self.statements.append(statement)
        return _CapturedResult(self.row)

    async def scalar(self, statement: Any) -> int:
        self.statements.append(statement)
        return 0


def _sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def test_postgresql_metrics_apply_agent_scope_to_every_runtime_source() -> None:
    agent_id = uuid4()
    tenant_id = uuid4()
    ended_at = datetime(2026, 9, 14, 12, tzinfo=UTC)
    started_at = ended_at - timedelta(hours=1)
    captured = _CapturingSession()
    session = cast(AsyncSession, captured)

    captured.row = (0, 0, 0, None, None, None)
    await SqlAlchemyObservabilityRepository._agent_run_metrics(  # pyright: ignore[reportPrivateUsage]
        session, tenant_id, started_at, ended_at, agent_id
    )
    assert "agent_runs.agent_id" in _sql(captured.statements[-1])

    captured.row = ()
    await SqlAlchemyObservabilityRepository._model_metrics(  # pyright: ignore[reportPrivateUsage]
        session, tenant_id, started_at, ended_at, agent_id
    )
    model_sql = _sql(captured.statements[-1])
    assert "JOIN agent_runs" in model_sql
    assert "agent_runs.agent_id" in model_sql
    assert "model_invocations.status IN ('failed', 'timed_out')" in model_sql

    captured.row = (0, None)
    await SqlAlchemyObservabilityRepository._queue_metrics(  # pyright: ignore[reportPrivateUsage]
        session, tenant_id, ended_at, agent_id
    )
    queue_sql = _sql(captured.statements[-1])
    assert "background_jobs.agent_id" in queue_sql
    assert str(agent_id) in queue_sql

    captured.row = (0, 0, 0, 0, 0)
    await SqlAlchemyObservabilityRepository._channel_delivery_metrics(  # pyright: ignore[reportPrivateUsage]
        session, tenant_id, started_at, ended_at, agent_id
    )
    channel_sql = _sql(captured.statements[-1])
    assert "JOIN channel_instances" in channel_sql
    assert "channel_instances.agent_id" in channel_sql

    captured.row = (0, 0, 0, 0, 0, 0)
    before = len(captured.statements)
    await SqlAlchemyObservabilityRepository._notification_delivery_metrics(  # pyright: ignore[reportPrivateUsage]
        session, tenant_id, started_at, ended_at, agent_id
    )
    notification_sql = "\n".join(_sql(item) for item in captured.statements[before:])
    assert notification_sql.count(str(agent_id)) == 2
    assert "replayed_from_id" in notification_sql
