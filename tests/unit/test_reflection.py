"""高精度反思、记忆写入判别与关系校准测试。"""

import pytest

from cnb_cognition import (
    DeterministicReflectionEngine,
    MemoryWriteDecision,
    MemoryWriteMode,
    ReflectionPolicy,
    RelationshipContext,
    RelationshipSignal,
)
from cnb_domain import MemoryKind, MemorySensitivity


def test_explicit_preference_becomes_traceable_semantic_memory() -> None:
    plan = DeterministicReflectionEngine().reflect("请记住，我喜欢周末去徒步。")

    assert plan.memory_decision is MemoryWriteDecision.WRITE
    assert plan.memory_kind is MemoryKind.SEMANTIC
    assert plan.memory_sensitivity is MemorySensitivity.NORMAL
    assert plan.memory_reason_codes == ("explicit_memory_request", "stable_preference")
    assert plan.memory_content == "用户明确表达了较稳定的信息：请记住，我喜欢周末去徒步。"
    assert plan.importance >= 0.45
    assert "徒步" not in plan.title
    assert "徒步" not in plan.episode_summary


@pytest.mark.parametrize(
    "secret_text",
    (
        "请记住我的密码是绝不能保存的测试值",
        "请记住这个令牌 sk-" + "1234567890abcdefghijklmnop",
        "please remember -----BEGIN " + "PRIVATE KEY-----",
    ),
)
def test_credential_content_never_becomes_memory_or_episode_excerpt(secret_text: str) -> None:
    plan = DeterministicReflectionEngine().reflect(secret_text)

    assert plan.memory_decision is MemoryWriteDecision.SKIP_CREDENTIAL
    assert plan.memory_kind is None
    assert plan.memory_sensitivity is None
    assert plan.memory_content is None
    assert plan.memory_reason_codes == ("credential_content",)
    assert secret_text not in plan.title
    assert secret_text not in plan.episode_summary


@pytest.mark.parametrize(
    ("text", "decision"),
    [
        ("你好", MemoryWriteDecision.SKIP_SMALL_TALK),
        ("你今天喜欢做什么？", MemoryWriteDecision.SKIP_QUESTION),
        ("我的生日是什么？", MemoryWriteDecision.SKIP_QUESTION),
        ("今天天气还不错", MemoryWriteDecision.SKIP_LOW_SIGNAL),
    ],
)
def test_high_precision_mode_skips_non_durable_content(
    text: str,
    decision: MemoryWriteDecision,
) -> None:
    plan = DeterministicReflectionEngine().reflect(text)

    assert plan.memory_decision is decision
    assert plan.memory_content is None


def test_balanced_mode_can_keep_substantial_self_disclosure_as_unconfirmed_episode() -> None:
    plan = DeterministicReflectionEngine().reflect(
        "最近我开始学习做木工，也在慢慢适应新的生活节奏。",
        policy=ReflectionPolicy(memory_write_mode=MemoryWriteMode.BALANCED),
    )

    assert plan.memory_decision is MemoryWriteDecision.WRITE
    assert plan.memory_kind is MemoryKind.EPISODIC
    assert plan.memory_sensitivity is MemorySensitivity.SENSITIVE
    assert plan.memory_reason_codes == ("self_disclosure",)


def test_boundary_is_canonicalized_for_proactive_policy() -> None:
    plan = DeterministicReflectionEngine().reflect("请不要主动联系我，我想安静一阵。")

    assert plan.memory_decision is MemoryWriteDecision.WRITE
    assert plan.memory_kind is MemoryKind.RELATIONAL
    assert plan.boundaries == ("不主动联系",)
    assert RelationshipSignal.BOUNDARY in plan.relationship_signals


def test_relationship_changes_are_signal_aware_and_saturate_near_limits() -> None:
    engine = DeterministicReflectionEngine()
    current = RelationshipContext(
        affinity=0.9,
        trust=0.8,
        familiarity=0.95,
        interaction_count=80,
    )

    warm = engine.reflect("谢谢你一直认真听我说。", relationship=current)
    hostile = engine.reflect("别烦我，滚开。", relationship=current)
    correction = engine.reflect("你记错了，我说的是另一件事。", relationship=current)

    assert warm.affinity_delta == pytest.approx(0.002)
    assert warm.trust_delta == 0
    assert 0 < warm.familiarity_delta < 0.001
    assert hostile.affinity_delta == pytest.approx(-0.04)
    assert hostile.trust_delta == pytest.approx(-0.02)
    assert hostile.familiarity_delta < warm.familiarity_delta
    assert correction.affinity_delta == 0
    assert correction.trust_delta == pytest.approx(-0.02)


def test_reflection_policy_and_relationship_context_validate_bounds() -> None:
    with pytest.raises(ValueError, match="正向关系步长"):
        ReflectionPolicy(positive_relationship_step=0.3)
    with pytest.raises(ValueError, match="熟悉度"):
        RelationshipContext(familiarity=1.1)
