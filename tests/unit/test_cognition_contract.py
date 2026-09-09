"""与框架无关的认知契约测试。"""

from cnb_cognition import AgentDecision


def test_agent_decision_has_no_implicit_side_effect() -> None:
    decision = AgentDecision(
        action="reply",
        rationale_summary="The user addressed the agent directly.",
        parameters={"tone": "warm"},
    )

    assert decision.action == "reply"
    assert decision.parameters == {"tone": "warm"}
