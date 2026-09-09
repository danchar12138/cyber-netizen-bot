"""拟人状态机、上下文预算、策略门和动态情绪测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cnb_cognition import (
    ActionCandidate,
    AffectState,
    AgentEvent,
    AnthropomorphicCognitiveRuntime,
    CognitiveAction,
    CognitiveContext,
    CognitiveStage,
    ContextAssembler,
    ContextFragment,
    ContextFragmentKind,
    ContextRole,
    DeterministicPolicyGate,
    MemoryRecallTraceItem,
    PolicyRuleSet,
    ToolPolicyContext,
    ToolRiskLevel,
)


def _event(text: str, *, at: datetime | None = None) -> AgentEvent:
    identifier = uuid4()
    return AgentEvent(
        event_id=identifier,
        tenant_id=identifier,
        agent_id=identifier,
        conversation_id=identifier,
        actor_id=identifier,
        occurred_at=at or datetime.now(UTC),
        event_type="message.received",
        text=text,
    )


def _context() -> CognitiveContext:
    identifier = uuid4()
    return CognitiveContext(
        run_id=identifier,
        configuration_version=1,
        persona_version=1,
        prompt_version=1,
    )


def test_context_assembler_keeps_required_and_high_priority_in_original_order() -> None:
    fragments = (
        ContextFragment(
            "old",
            ContextFragmentKind.RECENT_MESSAGE,
            ContextRole.USER,
            "旧消息",
            10,
            ordinal=1,
            token_estimate=3,
        ),
        ContextFragment(
            "persona",
            ContextFragmentKind.PERSONA,
            ContextRole.SYSTEM,
            "人格",
            90,
            ordinal=0,
            token_estimate=3,
        ),
        ContextFragment(
            "trigger",
            ContextFragmentKind.RECENT_MESSAGE,
            ContextRole.USER,
            "当前问题",
            100,
            required=True,
            ordinal=2,
            token_estimate=3,
        ),
    )

    result = ContextAssembler().assemble(fragments, token_budget=6)

    assert [item.fragment_id for item in result.fragments] == ["persona", "trigger"]
    assert result.omitted_fragment_ids == ("old",)
    assert result.truncated_fragment_ids == ()
    assert result.clipped is True


def test_context_assembler_truncates_oversized_required_fragment() -> None:
    fragment = ContextFragment(
        "trigger",
        ContextFragmentKind.RECENT_MESSAGE,
        ContextRole.USER,
        "很长的消息" * 100,
        100,
        required=True,
    )

    result = ContextAssembler().assemble((fragment,), token_budget=30)

    assert result.fragments[0].fragment_id == "trigger"
    assert "预算截断" in result.fragments[0].content
    assert result.truncated_fragment_ids == ("trigger",)
    assert result.estimated_tokens <= 30


def test_affect_state_decays_toward_neutral_baseline() -> None:
    start = datetime.now(UTC)
    affect = AffectState(valence=0.8, arousal=0.85, social_energy=0.35, updated_at=start)

    decayed = affect.decayed(start + timedelta(hours=6), half_life_seconds=21600)

    assert decayed.valence == 0.4
    assert decayed.arousal == 0.55
    assert decayed.social_energy == 0.55


def test_policy_gate_rejects_unapproved_external_side_effect() -> None:
    gate = DeterministicPolicyGate()
    external = ActionCandidate(
        action=CognitiveAction.TOOL,
        confidence=0.95,
        reason_summary="请求外部操作",
        tool_name="send_message",
        risk_level=ToolRiskLevel.HIGH,
        has_external_side_effect=True,
    )
    safe_reply = ActionCandidate(CognitiveAction.REPLY, 0.7, "解释当前限制")

    result = gate.evaluate((external, safe_reply), PolicyRuleSet())

    assert result.selected is safe_reply
    assert "未在当前策略允许列表" in result.rejected_reasons[0]


def test_policy_gate_checks_permission_network_budget_frequency_and_approval() -> None:
    gate = DeterministicPolicyGate()
    candidate = ActionCandidate(
        action=CognitiveAction.TOOL,
        confidence=1,
        reason_summary="读取受控服务",
        tool_name="controlled.read",
        risk_level=ToolRiskLevel.MEDIUM,
        required_permission="controlled:read",
        network_host="api.example.com",
        estimated_cost_units=2,
    )
    rules = PolicyRuleSet(
        allowed_tools=frozenset({"controlled.read"}),
        maximum_tool_risk=ToolRiskLevel.MEDIUM,
        network_allowlist=frozenset({"api.example.com"}),
        maximum_tool_calls_per_run=1,
        maximum_cost_units_per_run=2,
    )
    allowed_context = ToolPolicyContext(
        permissions=frozenset({"controlled:read"}),
        approved_tools=frozenset({"controlled.read"}),
    )

    allowed = gate.evaluate((candidate,), rules, allowed_context)
    missing_permission = gate.evaluate((candidate,), rules, ToolPolicyContext())

    assert allowed.selected is candidate
    assert missing_permission.selected.action is CognitiveAction.WAIT
    assert "缺少所需权限" in missing_permission.rejected_reasons[0]


async def test_runtime_can_ask_wait_not_reply_and_block_tool_candidate() -> None:
    runtime = AnthropomorphicCognitiveRuntime()

    clarification = await runtime.run(_event("帮帮我"), _context())
    silence = await runtime.run(_event("我想静静，不用回复"), _context())
    external = await runtime.run(_event("帮我发消息给所有人"), _context())

    assert clarification.action is CognitiveAction.ASK
    assert silence.action is CognitiveAction.NO_REPLY
    assert external.action is CognitiveAction.REPLY
    assert external.policy_evaluation is not None
    assert external.policy_evaluation.rejected_reasons
    assert [step.sequence for step in external.steps] == list(range(1, 8))
    assert "隐藏推理" not in external.rationale_summary


async def test_runtime_trace_records_memory_references_without_plaintext() -> None:
    runtime = AnthropomorphicCognitiveRuntime()
    memory_id = uuid4()
    context = _context()
    context = CognitiveContext(
        run_id=context.run_id,
        configuration_version=context.configuration_version,
        persona_version=context.persona_version,
        prompt_version=context.prompt_version,
        context_fragments=(
            ContextFragment(
                fragment_id=f"memory-{memory_id}",
                kind=ContextFragmentKind.LONG_TERM_MEMORY,
                role=ContextRole.SYSTEM,
                content="一段不应写入轨迹的记忆正文",
                priority=80,
            ),
        ),
        memory_recall_trace=(MemoryRecallTraceItem(memory_id, 0.8123456, 3),),
        relationship_version=4,
    )

    decision = await runtime.run(_event("还记得吗？"), context)
    step = next(item for item in decision.steps if item.stage is CognitiveStage.MEMORY_RECALL)

    assert step.detail["memory_ids"] == [str(memory_id)]
    assert step.detail["scores"] == [0.812346]
    assert step.detail["versions"] == [3]
    assert step.detail["relationship_version"] == 4
    assert "记忆正文" not in str(step.detail)


async def test_runtime_is_deterministic_for_same_state_and_event() -> None:
    runtime = AnthropomorphicCognitiveRuntime()
    event = _event("今天有点焦虑，我该怎么办？")
    context = _context()

    first = await runtime.run(event, context)
    replay = await runtime.run(event, context)

    assert first == replay
    assert first.social_mind is not None
    assert first.social_mind.tone == "沉稳、共情，不急着给结论"
