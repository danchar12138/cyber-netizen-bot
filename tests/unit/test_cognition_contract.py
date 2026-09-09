"""与框架无关的认知契约测试。"""

from datetime import UTC, datetime
from uuid import uuid4

from cnb_cognition import (
    AgentDecision,
    AgentEvent,
    CognitiveContext,
    MinimalCognitiveRuntime,
)


def test_agent_decision_has_no_implicit_side_effect() -> None:
    decision = AgentDecision(
        action="reply",
        rationale_summary="The user addressed the agent directly.",
        parameters={"tone": "warm"},
    )

    assert decision.action == "reply"
    assert decision.parameters == {"tone": "warm"}


async def test_minimal_runtime_waits_for_non_message_events() -> None:
    runtime = MinimalCognitiveRuntime()
    identifier = uuid4()
    decision = await runtime.run(
        AgentEvent(
            event_id=identifier,
            tenant_id=identifier,
            agent_id=identifier,
            conversation_id=identifier,
            actor_id=identifier,
            occurred_at=datetime.now(UTC),
            event_type="reaction.added",
        ),
        CognitiveContext(
            run_id=identifier,
            configuration_version=0,
            persona_version=1,
            prompt_version=1,
        ),
    )

    assert decision.action == "wait"
