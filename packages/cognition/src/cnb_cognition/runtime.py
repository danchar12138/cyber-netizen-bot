"""Stable boundary of the self-developed cognitive runtime."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Normalized event entering cognition."""

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
    """Immutable, source-traceable context selected for one run."""

    run_id: UUID
    configuration_version: int
    persona_version: int
    prompt_version: int
    context_fragments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentDecision:
    """Structured decision awaiting policy approval and realization."""

    action: str
    rationale_summary: str
    parameters: dict[str, str] = field(default_factory=lambda: {})


class CognitiveRuntime(Protocol):
    """Port implemented by the future multi-stage cognition engine."""

    async def run(self, event: AgentEvent, context: CognitiveContext) -> AgentDecision:
        """Produce a structured candidate action without external side effects."""
        ...
