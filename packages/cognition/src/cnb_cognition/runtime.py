"""自研认知运行时的稳定边界。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """进入认知流程的标准化事件。"""

    event_id: UUID
    tenant_id: UUID
    agent_id: UUID
    conversation_id: UUID
    actor_id: UUID
    occurred_at: datetime
    event_type: str
    text: str | None = None


@dataclass(frozen=True, slots=True)
class CognitiveContext:
    """为单次运行选择的不可变且可追溯来源的上下文。"""

    run_id: UUID
    configuration_version: int
    persona_version: int
    prompt_version: int
    context_fragments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """等待策略批准与表达实现的结构化决策。"""

    action: str
    rationale_summary: str
    parameters: dict[str, str] = field(default_factory=lambda: {})


class CognitiveRuntime(Protocol):
    """供后续多阶段认知引擎实现的端口。"""

    async def run(self, event: AgentEvent, context: CognitiveContext) -> AgentDecision:
        """生成不带外部副作用的结构化候选行动。"""
        ...


class MinimalCognitiveRuntime:
    """P1 使用的最小认知决策器，仅产生无副作用的回复候选。"""

    async def run(self, event: AgentEvent, context: CognitiveContext) -> AgentDecision:
        del context
        if event.event_type != "message.received" or not event.text:
            return AgentDecision(
                action="wait",
                rationale_summary="当前事件不需要生成文本回复。",
            )
        return AgentDecision(
            action="reply",
            rationale_summary="用户发送了可处理的文本消息。",
        )
