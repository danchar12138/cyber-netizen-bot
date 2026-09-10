"""反思、记忆维护和主动行为任务的无框架处理器。"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cnb_application.configuration_service import ConfigurationService
from cnb_application.memory_service import MemoryService, MemorySourceDraft
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
    JsonValue,
    MemoryConfirmation,
    MemoryKind,
    MemorySensitivity,
    MemorySourceKind,
    MemoryVisibility,
    PendingAgentRun,
    ScheduledActionStatus,
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
    ) -> None:
        self._repository = repository
        self._scheduled_actions = scheduled_actions
        self._configuration = configuration
        self._memory = memory

    async def handle(self, job: BackgroundJob) -> dict[str, JsonValue]:
        action_id = _uuid(job, "scheduled_action_id")
        action = await self._repository.get_scheduled_action(
            tenant_id=job.tenant_id,
            action_id=action_id,
        )
        if action is None:
            raise PermanentTaskError(f"定时行为不存在：{action_id}")
        if action.status is not ScheduledActionStatus.PENDING:
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
        return {
            "scheduled_action_id": str(decision.id),
            "status": decision.status.value,
            "score": decision.score,
            "decision_reasons": list(decision.decision_reasons),
        }


def _value(job: BackgroundJob, key: str) -> JsonValue:
    if key not in job.payload:
        raise PermanentTaskError(f"任务载荷缺少字段：{key}")
    return job.payload[key]


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
