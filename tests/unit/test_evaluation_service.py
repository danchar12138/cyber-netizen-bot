"""拟人自动回放、版本质量门与人工盲评服务测试。"""

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
from cnb_cognition import AnthropomorphicCognitiveRuntime
from cnb_domain import BlindReviewScore, EvaluationSuiteStatus
from cnb_infrastructure import (
    DevelopmentModelProvider,
    MemoryCognitionRepository,
    MemoryConfigurationRepository,
    MemoryEvaluationRepository,
)


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
