"""认知资源版本、运行回放与内置评测服务测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from cnb_application import CognitionService, CognitionValidationError
from cnb_domain import (
    CognitionResourceKind,
    CognitionVersionStatus,
    InvocationStatus,
    JsonValue,
)
from cnb_infrastructure import MemoryCognitionRepository


def _persona_payload() -> dict[str, object]:
    return {
        "identity": "自然又诚实的网友",
        "purpose": "长期交流",
        "principles": ["不捏造事实"],
        "boundaries": ["不泄露隐私"],
        "traits": {
            "warmth": 0.8,
            "curiosity": 0.7,
            "humor": 0.4,
            "directness": 0.6,
            "initiative": 0.5,
        },
        "style": {
            "address_style": "自然",
            "sentence_length": "短句",
            "emoji_frequency": "少量",
            "preferred_phrases": [],
            "avoided_phrases": [],
        },
    }


async def test_resource_can_be_tested_published_and_rolled_back() -> None:
    repository = MemoryCognitionRepository()
    agent_id = uuid4()
    tenant_id = uuid4()
    actor_id = uuid4()
    service = CognitionService(repository, agent_id=agent_id)

    draft = await service.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.PERSONA,
        key="default",
        name="主要人格",
        payload=_persona_payload(),  # pyright: ignore[reportArgumentType]
        note="初版",
        actor_id=actor_id,
    )
    published = await service.publish(
        resource_id=draft.id, tenant_id=tenant_id, agent_id=agent_id, actor_id=actor_id
    )
    rolled_back = await service.rollback(
        resource_id=draft.id, tenant_id=tenant_id, agent_id=agent_id, actor_id=actor_id
    )
    resources = await service.list_resources(tenant_id=tenant_id, agent_id=agent_id, kind=None)

    assert published.status is CognitionVersionStatus.PUBLISHED
    assert rolled_back.version == 2
    assert rolled_back.status is CognitionVersionStatus.PUBLISHED
    assert len(resources) == 2


def test_resource_rejects_secret_shaped_fields_and_invalid_traits() -> None:
    secret_payload: dict[str, JsonValue] = {
        "provider": "openai",
        "model": "gpt",
        "purposes": ["chat"],
        "api_key": "明文",
    }
    invalid_persona = _persona_payload()
    invalid_persona["traits"] = {
        "warmth": 2,
        "curiosity": 0.7,
        "humor": 0.4,
        "directness": 0.6,
        "initiative": 0.5,
    }

    with pytest.raises(CognitionValidationError, match="密钥"):
        CognitionService.validate_payload(CognitionResourceKind.MODEL_PROFILE, secret_payload)
    with pytest.raises(CognitionValidationError, match="warmth"):
        CognitionService.validate_payload(CognitionResourceKind.PERSONA, invalid_persona)  # pyright: ignore[reportArgumentType]


async def test_model_route_requires_published_profiles() -> None:
    repository = MemoryCognitionRepository()
    agent_id, tenant_id, actor_id = uuid4(), uuid4(), uuid4()
    service = CognitionService(repository, agent_id=agent_id)
    route = await service.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.MODEL_ROUTE,
        key="chat.realizer",
        name="对话路由",
        payload={
            "purpose": "chat.realizer",
            "primary_profile": {"key": "missing", "version": 1},
            "fallback_profiles": [],
            "timeout_seconds": 30,
            "max_attempts": 2,
        },
        note=None,
        actor_id=actor_id,
    )

    with pytest.raises(CognitionValidationError, match="模型档案版本不存在"):
        await service.publish(
            resource_id=route.id, tenant_id=tenant_id, agent_id=agent_id, actor_id=actor_id
        )


async def test_policy_freezes_an_exact_published_tool_version() -> None:
    repository = MemoryCognitionRepository()
    agent_id, tenant_id, actor_id = uuid4(), uuid4(), uuid4()
    service = CognitionService(repository, agent_id=agent_id)
    tool_payload: dict[str, JsonValue] = {
        "description": "读取公开资料",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "risk_level": "low",
        "has_external_side_effect": False,
        "enabled": True,
    }
    tool_v1 = await service.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.TOOL,
        key="public.read",
        name="公开资料读取",
        payload=tool_payload,
        note=None,
        actor_id=actor_id,
    )
    await service.publish(
        resource_id=tool_v1.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    tool_v2 = await service.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.TOOL,
        key="public.read",
        name="公开资料读取",
        payload=tool_payload,
        note="尚未发布",
        actor_id=actor_id,
    )
    policy_payload: dict[str, JsonValue] = {
        "allowed_tools": [{"key": "public.read", "version": tool_v1.version}],
        "maximum_tool_risk": "low",
        "allow_external_side_effects": False,
        "allow_no_reply": True,
        "network_allowlist": [],
        "maximum_tool_calls_per_run": 1,
        "maximum_cost_units_per_run": 10,
        "approval_required_at_or_above": "medium",
    }
    policy = await service.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.POLICY,
        key="default",
        name="默认策略",
        payload=policy_payload,
        note=None,
        actor_id=actor_id,
    )
    await service.publish(
        resource_id=policy.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    invalid_payload = dict(policy_payload)
    invalid_payload["allowed_tools"] = [{"key": "public.read", "version": tool_v2.version}]
    invalid_policy = await service.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.POLICY,
        key="unpublished-tool",
        name="引用未发布工具的策略",
        payload=invalid_payload,
        note=None,
        actor_id=actor_id,
    )

    bundle = await service.resolve_runtime_bundle(tenant_id=tenant_id, agent_id=agent_id)
    assert bundle.policy.allowed_tools == frozenset({"public.read"})
    with pytest.raises(CognitionValidationError, match="未发布或未启用"):
        await service.publish(
            resource_id=invalid_policy.id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
    with pytest.raises(CognitionValidationError, match="key/version"):
        CognitionService.validate_payload(
            CognitionResourceKind.POLICY,
            {**policy_payload, "allowed_tools": ["public.read"]},
        )


async def test_built_in_evaluation_suite_passes_every_boundary_case() -> None:
    service = CognitionService(MemoryCognitionRepository(), agent_id=uuid4())

    results = await service.run_evaluation_suite()

    assert len(results) == 5
    assert all(item.passed for item in results)


async def test_model_invocation_trace_is_tenant_isolated_without_persona_state() -> None:
    repository = MemoryCognitionRepository()
    owner_tenant = uuid4()
    run_id = uuid4()
    invocation = CognitionService.new_invocation(
        run_id=run_id,
        tenant_id=owner_tenant,
        purpose="chat.realizer",
        provider="development",
        model="friendly-echo-v1",
        attempt=1,
        status=InvocationStatus.FAILED,
        started_at=datetime.now(UTC),
        error_code="测试失败",
    )
    await repository.save_model_invocation(invocation)

    owner_trace = await repository.get_run_trace(run_id=run_id, tenant_id=owner_tenant)
    foreign_trace = await repository.get_run_trace(run_id=run_id, tenant_id=uuid4())

    assert owner_trace is not None
    assert owner_trace.model_invocations == (invocation,)
    assert foreign_trace is None
