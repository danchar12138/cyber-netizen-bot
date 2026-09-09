"""持久化最小对话闭环的应用服务与仓储端口。"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from cnb_application.configuration_service import ConfigurationService
from cnb_application.pagination import EntityCursor, decode_cursor, encode_cursor
from cnb_cognition import (
    AgentEvent,
    CognitiveContext,
    CognitiveRuntime,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
    ModelUsage,
)
from cnb_domain import (
    AgentRun,
    Conversation,
    ConversationEvent,
    DevelopmentIdentity,
    Message,
    MessageSenderType,
    MessageStatus,
    PendingAgentRun,
)


class ConversationNotFoundError(LookupError):
    """当前用户不能访问目标会话时抛出。"""


class AgentRunNotFoundError(LookupError):
    """当前用户不能访问目标 Agent Run 时抛出。"""


class ConversationConflictError(RuntimeError):
    """会话或运行状态不允许当前操作时抛出。"""


class ModelProviderConfigurationError(RuntimeError):
    """生效模型配置或所需凭证不完整时抛出。"""


@dataclass(frozen=True, slots=True)
class CursorPage[T]:
    """带可选下一页游标的键集分页结果。"""

    items: tuple[T, ...]
    next_cursor: str | None


class ConversationRepository(Protocol):
    """对话、消息、运行与有序事件的原子持久化边界。"""

    async def ensure_development_identity(
        self, identity: DevelopmentIdentity
    ) -> DevelopmentIdentity: ...

    async def list_conversations(
        self,
        *,
        user_id: UUID,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[Conversation, ...]: ...

    async def create_conversation(
        self,
        *,
        identity: DevelopmentIdentity,
        title: str,
    ) -> Conversation: ...

    async def get_conversation_for_user(
        self, conversation_id: UUID, user_id: UUID
    ) -> Conversation | None: ...

    async def list_messages(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        limit: int,
        cursor: EntityCursor | None,
    ) -> tuple[Message, ...]: ...

    async def begin_agent_run(
        self,
        *,
        identity: DevelopmentIdentity,
        conversation_id: UUID,
        client_message_id: UUID,
        content: str,
        configuration_version: int,
        persona_version: int,
        prompt_version: int,
        model_profile: str,
    ) -> PendingAgentRun: ...

    async def list_context_messages(
        self, *, conversation_id: UUID, user_id: UUID, limit: int
    ) -> tuple[Message, ...]: ...

    async def mark_run_started(self, run_id: UUID) -> AgentRun: ...

    async def append_run_delta(self, run_id: UUID, delta: str) -> Message: ...

    async def complete_run(self, run_id: UUID, usage: ModelUsage | None) -> AgentRun: ...

    async def fail_run(self, run_id: UUID, error_code: str) -> AgentRun: ...

    async def cancel_run(self, run_id: UUID, *, user_id: UUID) -> AgentRun: ...

    async def list_events(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> tuple[ConversationEvent, ...]: ...


class ModelProviderResolver(Protocol):
    """根据配置和运行作用域解析一个厂商无关模型 Provider。"""

    async def resolve(
        self,
        *,
        provider: str,
        model: str,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> ModelProvider: ...


class StaticModelProviderResolver:
    """将既有单一 Provider 适配到动态解析端口，便于测试和嵌入。"""

    def __init__(self, model_provider: ModelProvider) -> None:
        self._model_provider = model_provider

    async def resolve(
        self,
        *,
        provider: str,
        model: str,
        tenant_id: UUID,
        agent_id: UUID | None = None,
        channel_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> ModelProvider:
        del provider, model, tenant_id, agent_id, channel_id, user_id
        return self._model_provider


class ConversationService:
    """编排身份、持久化、认知决策和模型流式响应。"""

    def __init__(
        self,
        *,
        repository: ConversationRepository,
        runtime: CognitiveRuntime,
        model_provider_resolver: ModelProviderResolver,
        configuration_service: ConfigurationService,
        identity: DevelopmentIdentity,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._model_provider_resolver = model_provider_resolver
        self._configuration_service = configuration_service
        self._identity = identity

    @property
    def identity(self) -> DevelopmentIdentity:
        return self._identity

    async def ensure_development_identity(self) -> DevelopmentIdentity:
        return await self._repository.ensure_development_identity(self._identity)

    async def list_conversations(
        self, *, limit: int, cursor: str | None
    ) -> CursorPage[Conversation]:
        await self.ensure_development_identity()
        rows = await self._repository.list_conversations(
            user_id=self._identity.user_id,
            limit=limit + 1,
            cursor=decode_cursor(cursor),
        )
        return self._page(rows, limit, lambda item: EntityCursor(item.updated_at, item.id))

    async def create_conversation(self, *, title: str | None) -> Conversation:
        await self.ensure_development_identity()
        normalized_title = title.strip() if title and title.strip() else "新对话"
        return await self._repository.create_conversation(
            identity=self._identity,
            title=normalized_title,
        )

    async def get_conversation(self, conversation_id: UUID) -> Conversation:
        conversation = await self._repository.get_conversation_for_user(
            conversation_id, self._identity.user_id
        )
        if conversation is None:
            raise ConversationNotFoundError(f"会话不存在：{conversation_id}")
        return conversation

    async def list_messages(
        self, conversation_id: UUID, *, limit: int, cursor: str | None
    ) -> CursorPage[Message]:
        await self.get_conversation(conversation_id)
        rows = await self._repository.list_messages(
            conversation_id=conversation_id,
            user_id=self._identity.user_id,
            limit=limit + 1,
            cursor=decode_cursor(cursor),
        )
        page = self._page(rows, limit, lambda item: EntityCursor(item.created_at, item.id))
        return CursorPage(items=tuple(reversed(page.items)), next_cursor=page.next_cursor)

    async def send_message(
        self,
        conversation_id: UUID,
        *,
        client_message_id: UUID,
        content: str,
    ) -> PendingAgentRun:
        await self.get_conversation(conversation_id)
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=self._identity.tenant_id,
            agent_id=self._identity.agent_id,
            user_id=self._identity.user_id,
        )
        provider_name, model_name = self._model_selection(configuration.values)
        return await self._repository.begin_agent_run(
            identity=self._identity,
            conversation_id=conversation_id,
            client_message_id=client_message_id,
            content=content.strip(),
            configuration_version=configuration.version,
            persona_version=1,
            prompt_version=1,
            model_profile=f"{provider_name}/{model_name}",
        )

    async def execute_run(self, pending: PendingAgentRun) -> None:
        """执行已落盘的 Run，并把每个文本增量持久化为有序事件。"""
        if not pending.created:
            return
        usage: ModelUsage | None = None
        try:
            await self._repository.mark_run_started(pending.run.id)
            context_messages = await self._repository.list_context_messages(
                conversation_id=pending.conversation.id,
                user_id=self._identity.user_id,
                limit=40,
            )
            context = CognitiveContext(
                run_id=pending.run.id,
                configuration_version=pending.run.configuration_version,
                persona_version=pending.run.persona_version,
                prompt_version=pending.run.prompt_version,
                context_fragments=tuple(
                    item.content for item in context_messages if item.content.strip()
                ),
            )
            decision = await self._runtime.run(
                AgentEvent(
                    event_id=pending.trigger_message.id,
                    tenant_id=pending.run.tenant_id,
                    agent_id=pending.run.agent_id,
                    conversation_id=pending.run.conversation_id,
                    actor_id=self._identity.user_id,
                    occurred_at=pending.trigger_message.created_at,
                    event_type="message.received",
                    text=pending.trigger_message.content,
                ),
                context,
            )
            if decision.action != "reply":
                await self._repository.complete_run(pending.run.id, usage=None)
                return

            configuration = await self._configuration_service.resolve_effective(
                tenant_id=self._identity.tenant_id,
                agent_id=self._identity.agent_id,
                user_id=self._identity.user_id,
                version=pending.run.configuration_version,
            )
            system_prompt = configuration.values["persona.system_prompt"]
            max_output_tokens = configuration.values["model.chat.max_output_tokens"]
            if not isinstance(system_prompt, str) or not isinstance(max_output_tokens, int):
                raise ConversationConflictError("生效配置中的模型参数类型无效")
            provider_name, model_name = self._model_selection(configuration.values)
            model_provider = await self._model_provider_resolver.resolve(
                provider=provider_name,
                model=model_name,
                tenant_id=self._identity.tenant_id,
                agent_id=self._identity.agent_id,
                user_id=self._identity.user_id,
            )
            request = ModelRequest(
                messages=self._model_messages(context_messages, pending.response_message.id),
                instructions=system_prompt,
                max_output_tokens=max_output_tokens,
            )
            async for event in model_provider.stream(request):
                if event.delta:
                    await self._repository.append_run_delta(pending.run.id, event.delta)
                if event.usage is not None:
                    usage = event.usage
            await self._repository.complete_run(pending.run.id, usage)
        except asyncio.CancelledError:
            await self._repository.cancel_run(pending.run.id, user_id=self._identity.user_id)
            raise
        except Exception as error:
            await self._repository.fail_run(pending.run.id, type(error).__name__)

    async def cancel_run(self, run_id: UUID) -> AgentRun:
        return await self._repository.cancel_run(run_id, user_id=self._identity.user_id)

    async def list_events(
        self, conversation_id: UUID, *, after_sequence: int, limit: int = 200
    ) -> tuple[ConversationEvent, ...]:
        await self.get_conversation(conversation_id)
        return await self._repository.list_events(
            conversation_id=conversation_id,
            user_id=self._identity.user_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    @staticmethod
    def _page[T](
        rows: Sequence[T],
        limit: int,
        cursor_for: Callable[[T], EntityCursor],
    ) -> CursorPage[T]:
        visible = tuple(rows[:limit])
        next_cursor = (
            encode_cursor(cursor_for(visible[-1])) if len(rows) > limit and visible else None
        )
        return CursorPage(items=visible, next_cursor=next_cursor)

    @staticmethod
    def _model_messages(
        messages: Sequence[Message], response_message_id: UUID
    ) -> tuple[ModelMessage, ...]:
        mapped: list[ModelMessage] = []
        for message in messages:
            if message.id == response_message_id or not message.content.strip():
                continue
            if message.sender_type is MessageSenderType.SYSTEM:
                continue
            if (
                message.sender_type is MessageSenderType.AGENT
                and message.status is not MessageStatus.COMPLETED
            ):
                continue
            role = (
                ModelRole.USER
                if message.sender_type is MessageSenderType.USER
                else ModelRole.ASSISTANT
            )
            mapped.append(ModelMessage(role=role, content=message.content))
        return tuple(mapped)

    @staticmethod
    def _model_selection(values: Mapping[str, object]) -> tuple[str, str]:
        provider = values.get("model.chat.provider")
        if not isinstance(provider, str):
            raise ModelProviderConfigurationError("生效配置中的模型 Provider 无效")
        if provider == "development":
            return provider, "friendly-echo-v1"
        if provider == "openai":
            model = values.get("model.openai.model")
            if not isinstance(model, str) or not model.strip():
                raise ModelProviderConfigurationError("OpenAI 模型名称不能为空")
            return provider, model
        raise ModelProviderConfigurationError(f"不支持的模型 Provider：{provider}")
