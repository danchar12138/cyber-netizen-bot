"""反思、记忆维护和主动行为任务的无框架处理器。"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cnb_adapters import (
    NotificationAdapterError,
    NotificationAdapterRegistry,
    NotificationAdapterValidationError,
    NotificationDeliveryCommand,
)
from cnb_application.channel_service import ChannelDeliveryReceipt, ChannelService
from cnb_application.configuration_service import ConfigurationService, SecretStore
from cnb_application.memory_service import MemoryService, MemorySourceDraft
from cnb_application.observability_service import ObservabilityRepository
from cnb_application.task_service import (
    PermanentTaskError,
    ScheduledActionService,
    TaskRepository,
)
from cnb_cognition import (
    DeterministicReflectionEngine,
    MemoryWriteDecision,
    MemoryWriteMode,
    ProactiveContext,
    ProactivePolicy,
    ReflectionPolicy,
    RelationshipContext,
)
from cnb_domain import (
    BackgroundJob,
    ContentBlockKind,
    JsonValue,
    MemoryConfirmation,
    MemoryKind,
    MemorySensitivity,
    MemorySourceKind,
    MemoryVisibility,
    MultimodalContentBlock,
    PendingAgentRun,
    ScheduledAction,
    ScheduledActionKind,
    ScheduledActionStatus,
)


class NotificationDeliveryTaskHandler:
    """从安全任务摘要解析配置引用并执行通知 Adapter 投递。"""

    def __init__(
        self,
        adapters: NotificationAdapterRegistry,
        configuration: ConfigurationService,
        secret_store: SecretStore,
        observability_repository: ObservabilityRepository | None = None,
    ) -> None:
        self._adapters = adapters
        self._configuration = configuration
        self._secret_store = secret_store
        self._observability_repository = observability_repository

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        adapter_key = _string(job, "adapter")
        agent_id = _uuid(job, "agent_id")
        try:
            adapter = self._adapters.get(adapter_key)
        except NotificationAdapterValidationError as error:
            raise PermanentTaskError(str(error)) from error
        snapshot = await self._configuration.resolve_effective(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
        )
        payload = job.payload.get("delivery_payload")
        if not isinstance(payload, dict):
            raise PermanentTaskError("通知任务安全摘要无效")
        target, secret_key, settings = _notification_target(adapter_key, snapshot.values)
        if not target:
            raise PermanentTaskError("通知目标尚未配置")
        secret = None
        if secret_key is not None:
            secret = await self._secret_store.resolve_secret(
                secret_key,
                tenant_id=job.tenant_id,
                agent_id=agent_id,
            )
            if not secret and adapter_key != "email":
                raise PermanentTaskError("通知密钥尚未配置")
        try:
            result = await adapter.deliver(
                command=NotificationDeliveryCommand(
                    target=target,
                    payload=payload,
                    idempotency_key=_string(job, "idempotency_key"),
                    timeout_seconds=_number(job, "timeout_seconds"),
                    max_retries=_integer(job, "max_retries"),
                    settings=settings,
                ),
                secret=secret,
            )
        except NotificationAdapterValidationError as error:
            raise PermanentTaskError(str(error)) from error
        except NotificationAdapterError as error:
            if error.retryable:
                raise RuntimeError(error.code) from error
            raise PermanentTaskError(error.code) from error
        if result.delivered:
            await self._mark_observability_escalation(job)
        return {
            "adapter": adapter.key,
            "delivered": result.delivered,
            "attempts": result.attempts,
            "status_code": result.status_code,
            "idempotency_key": result.idempotency_key,
            "elapsed_ms": result.elapsed_ms,
        }

    async def _mark_observability_escalation(self, job: BackgroundJob) -> None:
        repository = self._observability_repository
        if repository is None:
            return
        raw_ids = job.payload.get("observability_lifecycle_ids")
        level = job.payload.get("observability_escalation_level")
        agent_id = job.payload.get("agent_id")
        if (
            not isinstance(raw_ids, list)
            or not isinstance(level, int)
            or isinstance(level, bool)
            or not isinstance(agent_id, str)
        ):
            return
        try:
            lifecycle_ids = tuple(UUID(value) for value in raw_ids if isinstance(value, str))
            parsed_agent_id = UUID(agent_id)
        except ValueError:
            return
        if lifecycle_ids:
            await repository.mark_observability_alert_lifecycles_escalated(
                tenant_id=job.tenant_id,
                agent_id=parsed_agent_id,
                lifecycle_ids=lifecycle_ids,
                escalation_level=level,
                escalated_at=datetime.now(UTC),
            )


class ReflectionSourceRepository(Protocol):
    """按完整作用域重新读取反思来源，任务载荷中的正文一律不可信。"""

    async def get_reflection_source(
        self,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        trigger_message_id: UUID,
        response_message_id: UUID,
        run_id: UUID | None,
    ) -> PendingAgentRun | None: ...


class ReflectionTaskHandler:
    """幂等生成 Episode、长期记忆和关系事件。"""

    def __init__(
        self,
        memory: MemoryService,
        sources: ReflectionSourceRepository,
        configuration: ConfigurationService,
        *,
        engine: DeterministicReflectionEngine | None = None,
    ) -> None:
        self._memory = memory
        self._sources = sources
        self._configuration = configuration
        self._engine = engine or DeterministicReflectionEngine()

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        agent_id = _uuid(job, "agent_id")
        user_id = _uuid(job, "user_id")
        conversation_id = _uuid(job, "conversation_id")
        trigger_message_id = _uuid(job, "trigger_message_id")
        response_message_id = _uuid(job, "response_message_id")
        source = await self._sources.get_reflection_source(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            response_message_id=response_message_id,
            run_id=_optional_uuid(job, "run_id"),
        )
        if source is None or source.trigger_message.sender_id is None:
            raise PermanentTaskError("反思来源不存在、尚未完成或不属于当前作用域")

        snapshot = await self._configuration.resolve_effective(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            version=source.run.configuration_version,
        )
        try:
            policy = ReflectionPolicy(
                memory_write_mode=MemoryWriteMode(
                    _setting_string(snapshot.values, "cognition.reflection.memory_write_mode")
                ),
                positive_relationship_step=_setting_number(
                    snapshot.values, "cognition.reflection.relationship_positive_step"
                ),
                negative_relationship_step=_setting_number(
                    snapshot.values, "cognition.reflection.relationship_negative_step"
                ),
                familiarity_step=_setting_number(
                    snapshot.values, "cognition.reflection.familiarity_step"
                ),
            )
        except ValueError as error:
            raise PermanentTaskError("反思策略配置无效") from error
        minimum_importance = _setting_number(
            snapshot.values, "cognition.reflection.minimum_importance"
        )
        current_detail = await self._memory.get_relationship(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            user_id=user_id,
        )
        current = current_detail.relationship if current_detail else None
        relationship_context = RelationshipContext(
            affinity=current.affinity if current else 0.0,
            trust=current.trust if current else 0.0,
            familiarity=current.familiarity if current else 0.0,
            interaction_count=current.interaction_count if current else 0,
            boundaries=current.boundaries if current else (),
        )
        text = source.trigger_message.content
        occurred_at = source.trigger_message.created_at
        actor_id = source.trigger_message.sender_id
        plan = self._engine.reflect(text, relationship=relationship_context, policy=policy)
        episode_id = uuid5(source.run.id, "reflection:episode")
        episode = await self._memory.create_episode(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            conversation_id=conversation_id,
            title=plan.title,
            summary=plan.episode_summary,
            started_at=occurred_at,
            ended_at=datetime.now(UTC),
            source_message_ids=(trigger_message_id,),
            actor_id=actor_id,
            entity_id=episode_id,
        )
        memory_id: UUID | None = None
        if (
            plan.memory_decision is MemoryWriteDecision.WRITE
            and plan.memory_kind is not None
            and plan.memory_sensitivity is not None
            and plan.memory_content is not None
            and plan.importance >= minimum_importance
        ):
            memory_id = uuid5(source.run.id, "reflection:memory")
            await self._memory.create_memory(
                tenant_id=job.tenant_id,
                agent_id=agent_id,
                user_id=user_id,
                conversation_id=conversation_id,
                episode_id=episode.id,
                kind=plan.memory_kind,
                visibility=MemoryVisibility.USER,
                content=plan.memory_content,
                event_at=occurred_at,
                confidence=plan.confidence,
                importance=plan.importance,
                emotional_weight=plan.emotional_weight,
                sensitivity=plan.memory_sensitivity,
                confirmation=MemoryConfirmation.UNCONFIRMED,
                sources=(
                    MemorySourceDraft(
                        kind=MemorySourceKind.MESSAGE,
                        source_id=str(trigger_message_id),
                        excerpt=text[:2000],
                        is_verbatim=True,
                        occurred_at=occurred_at,
                    ),
                ),
                actor_id=actor_id,
                entity_id=memory_id,
            )
        relationship = await self._memory.record_relationship_event(
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            event_type=f"conversation.reflected.{plan.relationship_signals[0].value}",
            affinity_delta=plan.affinity_delta,
            trust_delta=plan.trust_delta,
            familiarity_delta=plan.familiarity_delta,
            summary=plan.relationship_summary,
            boundaries=(
                tuple(dict.fromkeys((*relationship_context.boundaries, *plan.boundaries)))
                if plan.boundaries
                else None
            ),
            evidence_memory_id=memory_id,
            actor_id=actor_id,
            event_id=uuid5(source.run.id, "reflection:relationship-event"),
        )
        await self._memory.close_episode(
            episode_id=episode.id,
            tenant_id=job.tenant_id,
            agent_id=agent_id,
            consolidate=True,
            actor_id=actor_id,
        )
        return {
            "episode_id": str(episode.id),
            "memory_id": str(memory_id) if memory_id else None,
            "memory_created": memory_id is not None,
            "memory_decision": plan.memory_decision.value,
            "memory_kind": plan.memory_kind.value if plan.memory_kind else None,
            "memory_sensitivity": (
                plan.memory_sensitivity.value if plan.memory_sensitivity else None
            ),
            "memory_reason_codes": list(plan.memory_reason_codes),
            "relationship_signals": [item.value for item in plan.relationship_signals],
            "relationship_version": relationship.relationship.version,
        }


class EpisodeConsolidationTaskHandler:
    """异步关闭并巩固既有 Episode。"""

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        episode = await self._memory.close_episode(
            episode_id=_uuid(job, "episode_id"),
            tenant_id=job.tenant_id,
            agent_id=_uuid(job, "agent_id"),
            consolidate=True,
            actor_id=_uuid(job, "actor_id"),
        )
        return {"episode_id": str(episode.id), "status": episode.status.value}


class MemoryExtractionTaskHandler:
    """将经过上游清洗的候选异步写为未确认记忆。"""

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        occurred_at = _datetime(job, "occurred_at")
        source_id = _string(job, "source_id")
        detail = await self._memory.create_memory(
            tenant_id=job.tenant_id,
            agent_id=_uuid(job, "agent_id"),
            user_id=_optional_uuid(job, "user_id"),
            conversation_id=_optional_uuid(job, "conversation_id"),
            episode_id=_optional_uuid(job, "episode_id"),
            kind=MemoryKind(_string(job, "memory_kind")),
            visibility=MemoryVisibility(_string(job, "visibility")),
            content=_string(job, "content"),
            event_at=occurred_at,
            confidence=_number(job, "confidence"),
            importance=_number(job, "importance"),
            emotional_weight=_number(job, "emotional_weight"),
            sensitivity=MemorySensitivity(_string(job, "sensitivity")),
            confirmation=MemoryConfirmation.UNCONFIRMED,
            sources=(
                MemorySourceDraft(
                    kind=MemorySourceKind.REFLECTION,
                    source_id=source_id,
                    excerpt=None,
                    is_verbatim=False,
                    occurred_at=occurred_at,
                ),
            ),
            actor_id=_uuid(job, "actor_id"),
            entity_id=uuid5(job.id, "memory-extraction"),
        )
        return {"memory_id": str(detail.memory.id)}


class EmbeddingRebuildTaskHandler:
    """执行 API 已建立的向量重建进度记录。"""

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        result = await self._memory.run_embedding_rebuild(
            tenant_id=job.tenant_id,
            agent_id=_uuid(job, "agent_id"),
            job_id=_uuid(job, "index_job_id"),
        )
        return {
            "index_job_id": str(result.id),
            "processed_items": result.processed_items,
            "status": result.status.value,
        }


class RelationshipUpdateTaskHandler:
    """异步应用有界关系事件，并以任务 ID 保证事件幂等。"""

    def __init__(self, memory: MemoryService) -> None:
        self._memory = memory

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        result = await self._memory.record_relationship_event(
            tenant_id=job.tenant_id,
            agent_id=_uuid(job, "agent_id"),
            user_id=_uuid(job, "user_id"),
            event_type=_string(job, "event_type"),
            affinity_delta=_number(job, "affinity_delta"),
            trust_delta=_number(job, "trust_delta"),
            familiarity_delta=_number(job, "familiarity_delta"),
            summary=_string(job, "summary"),
            boundaries=None,
            evidence_memory_id=_optional_uuid(job, "evidence_memory_id"),
            actor_id=_uuid(job, "actor_id"),
            event_id=uuid5(job.id, "relationship-update"),
        )
        return {
            "relationship_id": str(result.relationship.id),
            "relationship_version": result.relationship.version,
        }


class ScheduledActionTaskHandler:
    """按最新用户配置、关系边界和社交预算评估定时行为。"""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        scheduled_actions: ScheduledActionService,
        configuration: ConfigurationService,
        memory: MemoryService,
        channel_service: ChannelService | None = None,
    ) -> None:
        self._repository = repository
        self._scheduled_actions = scheduled_actions
        self._configuration = configuration
        self._memory = memory
        self._channel_service = channel_service

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        action_id = _uuid(job, "scheduled_action_id")
        action = await self._repository.get_scheduled_action(
            tenant_id=job.tenant_id,
            action_id=action_id,
        )
        if action is None:
            raise PermanentTaskError(f"定时行为不存在：{action_id}")
        if action.status is not ScheduledActionStatus.PENDING:
            if (
                action.status is ScheduledActionStatus.DISPATCHED
                and action.kind is ScheduledActionKind.PROACTIVE_MESSAGE
            ):
                receipt = await self._dispatch_proactive(action)
                return self._delivery_result(action, receipt)
            return {"scheduled_action_id": str(action.id), "status": action.status.value}
        snapshot = await self._configuration.resolve_effective(
            tenant_id=job.tenant_id,
            agent_id=action.agent_id,
            user_id=action.user_id,
        )
        values = snapshot.values
        timezone_name = _setting_string(values, "system.default_timezone")
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise PermanentTaskError("用户时区配置无效") from error
        now = datetime.now(UTC)
        relationship = await self._memory.get_relationship(
            tenant_id=job.tenant_id,
            agent_id=action.agent_id,
            user_id=action.user_id,
        )
        relation = relationship.relationship if relationship else None
        boundary_block = bool(
            relation
            and any(
                marker in boundary.casefold()
                for boundary in relation.boundaries
                for marker in ("do_not_contact", "不主动联系", "禁止主动")
            )
        )
        last_activity = (
            _payload_datetime(action.payload, "last_user_activity_at") or action.created_at
        )
        if action.kind is ScheduledActionKind.PROACTIVE_MESSAGE:
            self._proactive_payload(action)
        decision = await self._scheduled_actions.evaluate(
            tenant_id=job.tenant_id,
            action_id=action.id,
            policy=ProactivePolicy(
                enabled=_setting_bool(values, "proactive.enabled"),
                minimum_score=_setting_number(values, "proactive.minimum_score"),
                daily_budget=_setting_int(values, "proactive.daily_message_budget"),
                quiet_hours_start=_setting_int(values, "proactive.quiet_hours_start"),
                quiet_hours_end=_setting_int(values, "proactive.quiet_hours_end"),
                require_recent_user_days=_setting_int(values, "proactive.require_recent_user_days"),
            ),
            context=ProactiveContext(
                local_hour=now.astimezone(timezone).hour,
                days_since_user_activity=max(0.0, (now - last_activity).total_seconds() / 86400),
                importance=_payload_number(action.payload, "importance", 0.5),
                confidence=_payload_number(action.payload, "confidence", 0.5),
                affinity=relation.affinity if relation else 0.0,
                trust=relation.trust if relation else 0.0,
                familiarity=relation.familiarity if relation else 0.0,
                used_budget=0,
                social_cost=action.social_cost,
                has_boundary_block=boundary_block,
            ),
            budget_date=now.astimezone(timezone).date(),
        )
        result: dict[str, JsonValue] = {
            "scheduled_action_id": str(decision.id),
            "status": decision.status.value,
            "score": decision.score,
            "decision_reasons": list(decision.decision_reasons),
        }
        if (
            decision.status is ScheduledActionStatus.DISPATCHED
            and decision.kind is ScheduledActionKind.PROACTIVE_MESSAGE
        ):
            result.update(self._delivery_result(decision, await self._dispatch_proactive(decision)))
        return result

    async def _dispatch_proactive(self, action: ScheduledAction) -> ChannelDeliveryReceipt:
        if self._channel_service is None:
            raise PermanentTaskError("主动消息渠道服务尚未配置")
        channel_id, recipient_id, message, thread_id, edit_message_id, request_streaming = (
            self._proactive_payload(action)
        )
        return await self._channel_service.deliver(
            tenant_id=action.tenant_id,
            agent_id=action.agent_id,
            channel_id=channel_id,
            recipient_id=recipient_id,
            blocks=(MultimodalContentBlock(kind=ContentBlockKind.TEXT, text=message),),
            idempotency_key=f"proactive:{action.id}",
            request_streaming=request_streaming,
            thread_id=thread_id,
            edit_message_id=edit_message_id,
            proactive=True,
        )

    @staticmethod
    def _delivery_result(
        action: ScheduledAction,
        receipt: ChannelDeliveryReceipt,
    ) -> dict[str, JsonValue]:
        return {
            "scheduled_action_id": str(action.id),
            "status": action.status.value,
            "delivery_status": receipt.status.value,
            "external_message_id": receipt.external_message_id,
            "delivery_degradations": list(receipt.degradations),
            "delivery_idempotent_replay": receipt.idempotent_replay,
        }

    @staticmethod
    def _proactive_payload(
        action: ScheduledAction,
    ) -> tuple[UUID, str, str, str | None, str | None, bool]:
        payload = action.payload
        channel_id = _action_uuid(payload, "channel_id")
        recipient_id = _action_text(payload, "recipient_id", 255)
        message = _action_text(payload, "message", 20_000)
        thread_id = _action_optional_text(payload, "thread_id", 255)
        edit_message_id = _action_optional_text(payload, "edit_message_id", 255)
        request_streaming = payload.get("request_streaming", False)
        if not isinstance(request_streaming, bool):
            raise PermanentTaskError("主动消息载荷 request_streaming 格式无效")
        return channel_id, recipient_id, message, thread_id, edit_message_id, request_streaming


def _value(job: BackgroundJob, key: str) -> JsonValue:
    if key not in job.payload:
        raise PermanentTaskError(f"任务载荷缺少字段：{key}")
    return job.payload[key]


def _action_value(payload: Mapping[str, JsonValue], key: str) -> JsonValue:
    value = payload.get(key)
    if value is None:
        raise PermanentTaskError(f"主动行为载荷缺少字段：{key}")
    return value


def _action_text(payload: Mapping[str, JsonValue], key: str, maximum: int) -> str:
    value = _action_value(payload, key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise PermanentTaskError(f"主动行为载荷字符串无效：{key}")
    return value.strip()


def _action_optional_text(payload: Mapping[str, JsonValue], key: str, maximum: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise PermanentTaskError(f"主动行为载荷字符串无效：{key}")
    return value.strip() or None


def _action_uuid(payload: Mapping[str, JsonValue], key: str) -> UUID:
    value = _action_text(payload, key, 36)
    try:
        return UUID(value)
    except ValueError as error:
        raise PermanentTaskError(f"主动行为载荷 UUID 无效：{key}") from error


def _string(job: BackgroundJob, key: str) -> str:
    value = _value(job, key)
    if not isinstance(value, str) or not value.strip():
        raise PermanentTaskError(f"任务载荷字段不是有效字符串：{key}")
    return value.strip()


def _uuid(job: BackgroundJob, key: str) -> UUID:
    try:
        return UUID(_string(job, key))
    except ValueError as error:
        raise PermanentTaskError(f"任务载荷字段不是有效 UUID：{key}") from error


def _optional_uuid(job: BackgroundJob, key: str) -> UUID | None:
    value = job.payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PermanentTaskError(f"任务载荷字段不是有效 UUID：{key}")
    try:
        return UUID(value)
    except ValueError as error:
        raise PermanentTaskError(f"任务载荷字段不是有效 UUID：{key}") from error


def _number(job: BackgroundJob, key: str) -> float:
    value = _value(job, key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PermanentTaskError(f"任务载荷字段不是有效数值：{key}")
    return float(value)


def _integer(job: BackgroundJob, key: str) -> int:
    value = _value(job, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PermanentTaskError(f"任务载荷字段不是有效整数：{key}")
    return value


def _notification_target(
    adapter: str,
    values: Mapping[str, JsonValue],
) -> tuple[str, str | None, dict[str, object]]:
    """从生效配置读取目标和密钥引用；绝不把 Secret 写入任务。"""
    if adapter == "webhook":
        return (
            _config_string(values, "alerts.notification.webhook_url"),
            ("alerts.notification.webhook_signing_secret"),
            {},
        )
    if adapter == "feishu_webhook":
        return (
            _config_string(values, "alerts.notification.feishu_webhook_url"),
            ("alerts.notification.feishu_signing_secret"),
            {},
        )
    if adapter == "email":
        return (
            _config_string(values, "alerts.notification.email.recipient"),
            "alerts.notification.email_password",
            {
                key.removeprefix("alerts.notification.email."): value
                for key, value in values.items()
                if key.startswith("alerts.notification.email.")
            },
        )
    raise PermanentTaskError(f"未知通知适配器：{adapter}")


def _config_string(values: Mapping[str, JsonValue], key: str) -> str:
    value = values.get(key)
    return value.strip() if isinstance(value, str) else ""


def _datetime(job: BackgroundJob, key: str) -> datetime:
    value = _string(job, key)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PermanentTaskError(f"任务载荷字段不是有效时间：{key}") from error
    if parsed.tzinfo is None:
        raise PermanentTaskError(f"任务载荷时间缺少时区：{key}")
    return parsed


def _payload_datetime(payload: dict[str, JsonValue], key: str) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PermanentTaskError(f"主动行为载荷时间无效：{key}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PermanentTaskError(f"主动行为载荷时间无效：{key}") from error
    if parsed.tzinfo is None:
        raise PermanentTaskError(f"主动行为载荷时间缺少时区：{key}")
    return parsed


def _payload_number(payload: dict[str, JsonValue], key: str, default: float) -> float:
    value = payload.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PermanentTaskError(f"主动行为载荷数值无效：{key}")
    return float(value)


def _setting_string(values: Mapping[str, JsonValue], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PermanentTaskError(f"生效配置字符串无效：{key}")
    return value


def _setting_int(values: Mapping[str, JsonValue], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PermanentTaskError(f"生效配置整数无效：{key}")
    return value


def _setting_number(values: Mapping[str, JsonValue], key: str) -> float:
    value = values.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PermanentTaskError(f"生效配置数值无效：{key}")
    return float(value)


def _setting_bool(values: Mapping[str, JsonValue], key: str) -> bool:
    value = values.get(key)
    if not isinstance(value, bool):
        raise PermanentTaskError(f"生效配置布尔值无效：{key}")
    return value
