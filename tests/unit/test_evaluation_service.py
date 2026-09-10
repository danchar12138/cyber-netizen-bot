"""拟人自动回放、版本质量门与人工盲评服务测试。"""

from datetime import datetime
from uuid import UUID, uuid4

import pytest

from cnb_application import (
    CognitionService,
    ConfigurationService,
    EvaluationCaseDraft,
    EvaluationConflictError,
    EvaluationNotFoundError,
    EvaluationService,
    EvaluationValidationError,
    StaticModelProviderResolver,
    build_default_registry,
)
from cnb_cognition import (
    AgentDecision,
    AgentEvent,
    AnthropomorphicCognitiveRuntime,
    CognitiveContext,
)
from cnb_domain import (
    BlindReviewScore,
    CognitionResourceKind,
    EvaluationSuiteStatus,
    JsonValue,
)
from cnb_infrastructure import (
    DevelopmentModelProvider,
    MemoryCognitionRepository,
    MemoryConfigurationRepository,
    MemoryEvaluationRepository,
)


class EventRecordingRuntime:
    """记录多候选回放事件，并复用真实拟人认知内核。"""

    def __init__(self) -> None:
        self._delegate = AnthropomorphicCognitiveRuntime()
        self.events: list[tuple[UUID, UUID, datetime]] = []

    async def run(self, event: AgentEvent, context: CognitiveContext) -> AgentDecision:
        self.events.append((event.event_id, event.conversation_id, event.occurred_at))
        return await self._delegate.run(event, context)


async def _comparison_service() -> tuple[
    EvaluationService,
    EventRecordingRuntime,
    MemoryEvaluationRepository,
    UUID,
]:
    """建立包含两个已发布档案的真实模型路由。"""
    agent_id = uuid4()
    tenant_id = uuid4()
    actor_id = uuid4()
    cognition_repository = MemoryCognitionRepository()
    cognition = CognitionService(cognition_repository, agent_id=agent_id)
    references: list[JsonValue] = []
    for key, model, input_price, output_price in (
        ("fast", "friendly-fast-v1", 1.0, 2.0),
        ("quality", "friendly-quality-v1", 3.0, 6.0),
    ):
        draft = await cognition.create_draft(
            tenant_id=tenant_id,
            agent_id=agent_id,
            kind=CognitionResourceKind.MODEL_PROFILE,
            key=key,
            name=f"{key} 对比档案",
            payload={
                "provider": "development",
                "model": model,
                "purposes": ["chat.realizer"],
                "pricing": {
                    "input_usd_per_million_tokens": input_price,
                    "output_usd_per_million_tokens": output_price,
                },
            },
            note=None,
            actor_id=actor_id,
        )
        published = await cognition.publish(
            resource_id=draft.id,
            tenant_id=tenant_id,
            agent_id=agent_id,
            actor_id=actor_id,
        )
        references.append({"key": key, "version": published.version})
    route = await cognition.create_draft(
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=CognitionResourceKind.MODEL_ROUTE,
        key="chat.realizer",
        name="多模型对比路由",
        payload={
            "purpose": "chat.realizer",
            "primary_profile": references[0],
            "fallback_profiles": references[1:],
            "timeout_seconds": 30,
            "max_attempts": 2,
        },
        note=None,
        actor_id=actor_id,
    )
    await cognition.publish(
        resource_id=route.id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_id=actor_id,
    )
    repository = MemoryEvaluationRepository()
    runtime = EventRecordingRuntime()
    service = EvaluationService(
        repository,
        runtime=runtime,
        cognition_service=cognition,
        configuration_service=ConfigurationService(
            build_default_registry(), MemoryConfigurationRepository()
        ),
        model_provider_resolver=StaticModelProviderResolver(DevelopmentModelProvider()),
        agent_id=agent_id,
    )
    return service, runtime, repository, tenant_id


def _service(agent_id: UUID | None = None) -> EvaluationService:
    resolved_agent_id = agent_id if agent_id is not None else uuid4()
    cognition_repository = MemoryCognitionRepository()
    configuration_repository = MemoryConfigurationRepository()
    cognition_service = CognitionService(
        cognition_repository,
        agent_id=resolved_agent_id,
    )
    return EvaluationService(
        MemoryEvaluationRepository(),
        runtime=AnthropomorphicCognitiveRuntime(),
        cognition_service=cognition_service,
        configuration_service=ConfigurationService(
            build_default_registry(), configuration_repository
        ),
        model_provider_resolver=StaticModelProviderResolver(DevelopmentModelProvider()),
        agent_id=resolved_agent_id,
    )


async def test_builtin_replay_freezes_versions_and_feeds_blind_report() -> None:
    service = _service()
    tenant_id = uuid4()
    actor_id = uuid4()

    run = await service.run_suite(tenant_id=tenant_id, actor_id=actor_id)

    assert run.gate_passed is True
    assert run.passed == run.total == 5
    assert run.configuration_version == 0
    assert run.provider == "development"
    assert run.input_tokens > 0
    assert run.output_tokens > 0
    assert run.estimated_cost_microusd == 0
    assert run.results[2].candidate_response is None

    assignment = await service.claim_blind_assignment(
        tenant_id=tenant_id,
        reviewer_id=actor_id,
        run_id=run.id,
    )
    assert assignment is not None
    assert assignment.response_a != assignment.response_b
    same_assignment = await service.claim_blind_assignment(
        tenant_id=tenant_id,
        reviewer_id=actor_id,
        run_id=run.id,
    )
    assert same_assignment == assignment

    score_a = BlindReviewScore(4, 4, 3, 5)
    score_b = BlindReviewScore(3, 3, 4, 5)
    review = await service.submit_blind_review(
        assignment_id=assignment.id,
        tenant_id=tenant_id,
        reviewer_id=actor_id,
        displayed_preference="tie",
        response_a_score=score_a,
        response_b_score=score_b,
        note="两边各有优点",
    )
    assert review.preference == "tie"
    report = await service.get_report(tenant_id=tenant_id, reviewer_id=actor_id)
    assert report.total_runs == 1
    assert report.gate_passed_runs == 1
    assert report.completed_reviews == 1
    assert report.pending_reviews == 3
    assert report.ties == 1
    with pytest.raises(EvaluationConflictError, match="已经提交"):
        await service.submit_blind_review(
            assignment_id=assignment.id,
            tenant_id=tenant_id,
            reviewer_id=actor_id,
            displayed_preference="a",
            response_a_score=score_a,
            response_b_score=score_b,
            note=None,
        )


async def test_custom_suite_is_versioned_published_and_tenant_isolated() -> None:
    service = _service()
    tenant_id = uuid4()
    actor_id = uuid4()
    case = EvaluationCaseDraft(
        case_key="custom-natural",
        category="自然度",
        input_text="今天有点累",
        expected_action="reply",
        reference_response="那先别逼自己满格运转。今天最耗你的是什么？",
        forbidden_phrases=("作为一个人工智能",),
    )

    first = await service.create_suite(
        tenant_id=tenant_id,
        key="custom",
        name="自定义回归",
        description="第一版",
        minimum_pass_rate=100,
        max_output_tokens=256,
        cases=(case,),
        actor_id=actor_id,
    )
    second = await service.create_suite(
        tenant_id=tenant_id,
        key="custom",
        name="自定义回归",
        description="第二版",
        minimum_pass_rate=100,
        max_output_tokens=256,
        cases=(case,),
        actor_id=actor_id,
    )
    published_first = await service.publish_suite(
        suite_id=first.id,
        tenant_id=tenant_id,
        actor_id=actor_id,
    )
    published_second = await service.publish_suite(
        suite_id=second.id,
        tenant_id=tenant_id,
        actor_id=actor_id,
    )
    suites = await service.list_suites(tenant_id=tenant_id)

    assert first.version == 1
    assert second.version == 2
    assert published_first.status is EvaluationSuiteStatus.PUBLISHED
    assert published_second.status is EvaluationSuiteStatus.PUBLISHED
    superseded_first = next(item for item in suites if item.id == first.id)
    assert superseded_first.status is EvaluationSuiteStatus.SUPERSEDED
    run = await service.run_suite(
        tenant_id=tenant_id,
        actor_id=actor_id,
        suite_id=second.id,
    )
    assert run.suite_version == 2
    assert run.total == 1
    with pytest.raises(EvaluationNotFoundError):
        await service.get_run(run_id=run.id, tenant_id=uuid4())


async def test_suite_validation_requires_reference_for_repliable_cases() -> None:
    service = _service()
    with pytest.raises(EvaluationValidationError, match="参考回答"):
        await service.create_suite(
            tenant_id=uuid4(),
            key="invalid",
            name="无效评测集",
            description=None,
            minimum_pass_rate=100,
            max_output_tokens=512,
            cases=(
                EvaluationCaseDraft(
                    case_key="missing-reference",
                    category="自然度",
                    input_text="你好",
                    expected_action="reply",
                ),
            ),
            actor_id=uuid4(),
        )


async def test_multi_model_comparison_uses_shared_snapshot_and_events() -> None:
    service, runtime, _, tenant_id = await _comparison_service()
    actor_id = uuid4()

    targets = await service.list_comparison_targets(tenant_id=tenant_id)
    comparison = await service.run_comparison(
        tenant_id=tenant_id,
        actor_id=actor_id,
        profile_keys=("fast", "quality"),
    )

    assert [(item.profile_key, item.profile_version) for item in targets] == [
        ("fast", 1),
        ("quality", 1),
    ]
    assert [entry.profile_key for entry in comparison.entries] == ["fast", "quality"]
    assert [entry.run.model for entry in comparison.entries] == [
        "friendly-fast-v1",
        "friendly-quality-v1",
    ]
    assert all(entry.run.total == 5 for entry in comparison.entries)
    assert all(
        entry.run.configuration_version == comparison.configuration_version
        and entry.run.persona_version == comparison.persona_version
        and entry.run.prompt_version == comparison.prompt_version
        and entry.run.policy_version == comparison.policy_version
        and entry.run.model_route_version == comparison.model_route_version
        for entry in comparison.entries
    )
    first_events = runtime.events[:5]
    second_events = runtime.events[5:]
    assert first_events == second_events
    assert comparison.entries[1].run.estimated_cost_microusd > (
        comparison.entries[0].run.estimated_cost_microusd
    )

    summaries = await service.list_comparisons(tenant_id=tenant_id)
    detail = await service.get_comparison(
        comparison_id=comparison.id,
        tenant_id=tenant_id,
    )
    assert summaries[0].id == comparison.id
    assert all(not entry.run.results for entry in summaries[0].entries)
    assert detail == comparison
    with pytest.raises(EvaluationNotFoundError):
        await service.get_comparison(
            comparison_id=comparison.id,
            tenant_id=uuid4(),
        )


async def test_multi_model_comparison_rejects_invalid_or_unpublished_targets() -> None:
    service, _, _, tenant_id = await _comparison_service()
    actor_id = uuid4()

    with pytest.raises(EvaluationValidationError, match="至少需要选择两个"):
        await service.run_comparison(
            tenant_id=tenant_id,
            actor_id=actor_id,
            profile_keys=("fast",),
        )
    with pytest.raises(EvaluationValidationError, match="不能重复"):
        await service.run_comparison(
            tenant_id=tenant_id,
            actor_id=actor_id,
            profile_keys=("fast", "fast"),
        )
    with pytest.raises(EvaluationValidationError, match="不能超过 4 个"):
        await service.run_comparison(
            tenant_id=tenant_id,
            actor_id=actor_id,
            profile_keys=("fast", "quality", "third", "fourth", "fifth"),
        )
    with pytest.raises(EvaluationValidationError, match="不属于当前已发布路由"):
        await service.run_comparison(
            tenant_id=tenant_id,
            actor_id=actor_id,
            profile_keys=("fast", "未发布档案"),
        )


async def test_multi_model_comparison_requires_published_route() -> None:
    service = _service()
    tenant_id = uuid4()

    assert await service.list_comparison_targets(tenant_id=tenant_id) == ()
    with pytest.raises(EvaluationValidationError, match="尚未发布"):
        await service.run_comparison(
            tenant_id=tenant_id,
            actor_id=uuid4(),
            profile_keys=("fast", "quality"),
        )
