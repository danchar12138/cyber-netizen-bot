from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    ChannelDeliveryReceipt,
    ChannelService,
    ConfigurationService,
    EffectiveConfigurationSnapshot,
    MemoryService,
    PermanentTaskError,
    ScheduledActionService,
    ScheduledActionTaskHandler,
    TaskRepository,
)
from cnb_domain import (
    BackgroundJob,
    BackgroundJobKind,
    BackgroundJobStatus,
    ChannelEventStatus,
    JsonValue,
    ScheduledAction,
    ScheduledActionKind,
    ScheduledActionStatus,
)
from cnb_infrastructure import InMemoryMemoryRepository

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
AGENT_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
CHANNEL_ID = UUID("44444444-4444-4444-8444-444444444444")


class RecordingChannelService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def deliver(self, **kwargs: object) -> ChannelDeliveryReceipt:
        self.calls.append(kwargs)
        return ChannelDeliveryReceipt(
            status=ChannelEventStatus.DELIVERED,
            external_message_id=f"web-{len(self.calls)}",
            degradations=(),
            delivered_at=datetime.now(UTC),
            idempotent_replay=len(self.calls) > 1,
        )


class RecordingTaskRepository:
    def __init__(self, action: ScheduledAction) -> None:
        self.action = action

    async def get_scheduled_action(
        self, *, tenant_id: UUID, action_id: UUID
    ) -> ScheduledAction | None:
        if tenant_id != self.action.tenant_id or action_id != self.action.id:
            return None
        return self.action


class ImmediateApprovalService:
    def __init__(self, repository: RecordingTaskRepository) -> None:
        self._repository = repository

    async def evaluate(self, **_: object) -> ScheduledAction:
        self._repository.action = replace(
            self._repository.action,
            status=ScheduledActionStatus.DISPATCHED,
            score=0.95,
            decision_reasons=("policy_approved",),
            completed_at=datetime.now(UTC),
        )
        return self._repository.action


class StaticConfiguration:
    async def resolve_effective(self, **_: object) -> EffectiveConfigurationSnapshot:
        return EffectiveConfigurationSnapshot(
            version=1,
            values={
                "system.default_timezone": "UTC",
                "proactive.enabled": True,
                "proactive.minimum_score": 0.7,
                "proactive.daily_message_budget": 2,
                "proactive.quiet_hours_start": 22,
                "proactive.quiet_hours_end": 8,
                "proactive.require_recent_user_days": 30,
            },
            sources={},
        )


def _action(*, payload: dict[str, JsonValue]) -> ScheduledAction:
    now = datetime.now(UTC)
    return ScheduledAction(
        id=uuid4(),
        tenant_id=TENANT_ID,
        agent_id=AGENT_ID,
        user_id=USER_ID,
        conversation_id=None,
        kind=ScheduledActionKind.PROACTIVE_MESSAGE,
        status=ScheduledActionStatus.PENDING,
        scheduled_for=now,
        expires_at=now + timedelta(hours=1),
        idempotency_key="proactive:test",
        reason="测试主动消息",
        payload=payload,
        score=None,
        social_cost=1,
        decision_reasons=(),
        job_id=None,
        created_by=USER_ID,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )


def _job(action: ScheduledAction) -> BackgroundJob:
    now = datetime.now(UTC)
    return BackgroundJob(
        id=uuid4(),
        tenant_id=TENANT_ID,
        kind=BackgroundJobKind.SCHEDULED_ACTION,
        queue="proactive",
        status=BackgroundJobStatus.PENDING,
        payload={"scheduled_action_id": str(action.id)},
        deduplication_key="scheduled-action:proactive:test",
        source_inbox_id=None,
        correlation_id=str(action.id),
        attempt_count=0,
        max_attempts=3,
        lease_seconds=60,
        retry_base_seconds=2,
        available_at=now,
        lease_owner=None,
        lease_expires_at=None,
        cancel_requested_at=None,
        last_error_code=None,
        last_error_summary=None,
        result_summary={},
        replayed_from_id=None,
        created_by=USER_ID,
        created_at=now,
        started_at=None,
        completed_at=None,
        updated_at=now,
    )


def _handler(
    repository: RecordingTaskRepository,
    channel: RecordingChannelService | None,
) -> ScheduledActionTaskHandler:
    return ScheduledActionTaskHandler(
        repository=cast(TaskRepository, repository),
        scheduled_actions=cast(ScheduledActionService, ImmediateApprovalService(repository)),
        configuration=cast(ConfigurationService, StaticConfiguration()),
        memory=MemoryService(InMemoryMemoryRepository()),
        channel_service=cast(ChannelService, channel) if channel is not None else None,
    )


@pytest.mark.asyncio
async def test_approved_proactive_action_delivers_and_replays_by_stable_key() -> None:
    action = _action(
        payload={
            "channel_id": str(CHANNEL_ID),
            "recipient_id": "web-user-1",
            "message": "提醒你查看今天的计划",
            "request_streaming": False,
        }
    )
    repository = RecordingTaskRepository(action)
    channel = RecordingChannelService()
    handler = _handler(repository, channel)

    first = await handler.handle(_job(action))
    replay = await handler.handle(_job(action))

    assert first["status"] == "dispatched"
    assert first["delivery_status"] == "delivered"
    assert first["delivery_idempotent_replay"] is False
    assert replay["delivery_idempotent_replay"] is True
    assert len(channel.calls) == 2
    assert channel.calls[0]["idempotency_key"] == f"proactive:{action.id}"
    assert channel.calls[0]["recipient_id"] == "web-user-1"
    assert "message" not in first


@pytest.mark.asyncio
async def test_proactive_payload_is_validated_before_policy_evaluation() -> None:
    action = _action(
        payload={
            "channel_id": str(CHANNEL_ID),
            "recipient_id": "web-user-1",
        }
    )
    repository = RecordingTaskRepository(action)

    with pytest.raises(PermanentTaskError, match="message"):
        await _handler(repository, RecordingChannelService()).handle(_job(action))
