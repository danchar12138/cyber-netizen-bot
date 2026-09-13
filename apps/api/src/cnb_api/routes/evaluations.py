"""版本化拟人自动回放、质量门和匿名人工盲评 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from cnb_api.dependencies import (
    get_admin_principal,
    get_evaluation_service,
    require_permission,
)
from cnb_application import (
    EvaluationCaseDraft,
    EvaluationConflictError,
    EvaluationNotFoundError,
    EvaluationService,
    EvaluationValidationError,
)
from cnb_contracts import (
    BlindReviewAssignmentCreate,
    BlindReviewAssignmentResponse,
    BlindReviewResponse,
    BlindReviewSubmit,
    EvaluationComparisonCreate,
    EvaluationComparisonListResponse,
    EvaluationComparisonResponse,
    EvaluationComparisonSummaryResponse,
    EvaluationModelTargetListResponse,
    EvaluationModelTargetResponse,
    EvaluationReportResponse,
    EvaluationRunCreate,
    EvaluationRunListResponse,
    EvaluationRunResponse,
    EvaluationRunSummaryResponse,
    EvaluationSuiteCreate,
    EvaluationSuiteDefinitionResponse,
    EvaluationSuiteListResponse,
)
from cnb_domain import AdminPermission, AdminPrincipal, BlindReviewScore

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.get(
    "/comparison-targets",
    response_model=EvaluationModelTargetListResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def list_evaluation_comparison_targets(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationModelTargetListResponse:
    """列出当前发布路由允许用于同源回放的模型档案。"""
    items = await service.list_comparison_targets(tenant_id=principal.tenant_id)
    return EvaluationModelTargetListResponse(
        items=tuple(
            EvaluationModelTargetResponse.model_validate(item, from_attributes=True)
            for item in items
        )
    )


@router.get(
    "/suites",
    response_model=EvaluationSuiteListResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_READ))],
)
async def list_evaluation_suites(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationSuiteListResponse:
    """列出当前智能体的全部评测集版本。"""
    items = await service.list_suites(tenant_id=principal.tenant_id)
    return EvaluationSuiteListResponse(
        items=tuple(
            EvaluationSuiteDefinitionResponse.model_validate(item, from_attributes=True)
            for item in items
        )
    )


@router.post(
    "/suites",
    response_model=EvaluationSuiteDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_WRITE))],
)
async def create_evaluation_suite(
    command: EvaluationSuiteCreate,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationSuiteDefinitionResponse:
    """一次性创建包含完整用例快照的不可变评测集草稿。"""
    try:
        item = await service.create_suite(
            tenant_id=principal.tenant_id,
            key=command.key,
            name=command.name,
            description=command.description,
            minimum_pass_rate=command.minimum_pass_rate,
            max_output_tokens=command.max_output_tokens,
            cases=tuple(
                EvaluationCaseDraft(
                    case_key=case.case_key,
                    category=case.category,
                    input_text=case.input_text,
                    expected_action=case.expected_action,
                    reference_response=case.reference_response,
                    required_phrases=case.required_phrases,
                    forbidden_phrases=case.forbidden_phrases,
                )
                for case in command.cases
            ),
            actor_id=principal.user_id,
        )
    except EvaluationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return EvaluationSuiteDefinitionResponse.model_validate(item, from_attributes=True)


@router.post(
    "/suites/{suite_id}/publish",
    response_model=EvaluationSuiteDefinitionResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_WRITE))],
)
async def publish_evaluation_suite(
    suite_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationSuiteDefinitionResponse:
    """发布草稿并原子替代同键的上一发布版本。"""
    try:
        item = await service.publish_suite(
            suite_id=suite_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
        )
    except EvaluationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except EvaluationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return EvaluationSuiteDefinitionResponse.model_validate(item, from_attributes=True)


@router.post(
    "/runs",
    response_model=EvaluationRunResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def run_evaluation(
    command: EvaluationRunCreate,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationRunResponse:
    """运行内置基线或指定已发布评测集并冻结版本、回答与成本。"""
    try:
        run = await service.run_suite(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            suite_id=command.suite_id,
        )
    except EvaluationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except EvaluationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except EvaluationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return EvaluationRunResponse.model_validate(run, from_attributes=True)


@router.get(
    "/runs",
    response_model=EvaluationRunListResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def list_evaluation_runs(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationRunListResponse:
    """列出最近自动回放的轻量摘要。"""
    runs = await service.list_runs(tenant_id=principal.tenant_id, limit=limit)
    return EvaluationRunListResponse(
        items=tuple(
            EvaluationRunSummaryResponse.model_validate(item, from_attributes=True) for item in runs
        )
    )


@router.get(
    "/runs/{run_id}",
    response_model=EvaluationRunResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def get_evaluation_run(
    run_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationRunResponse:
    """读取自动质量门、冻结输出和全部确定性检查。"""
    try:
        run = await service.get_run(run_id=run_id, tenant_id=principal.tenant_id)
    except EvaluationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return EvaluationRunResponse.model_validate(run, from_attributes=True)


@router.post(
    "/comparisons",
    response_model=EvaluationComparisonResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def run_evaluation_comparison(
    command: EvaluationComparisonCreate,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationComparisonResponse:
    """在同一冻结资源快照上运行多个已发布模型档案。"""
    try:
        comparison = await service.run_comparison(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            suite_id=command.suite_id,
            profile_keys=command.profile_keys,
        )
    except EvaluationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except EvaluationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except EvaluationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return EvaluationComparisonResponse.model_validate(comparison, from_attributes=True)


@router.get(
    "/comparisons",
    response_model=EvaluationComparisonListResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def list_evaluation_comparisons(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EvaluationComparisonListResponse:
    """列出不携带回答正文的最近多模型对比摘要。"""
    comparisons = await service.list_comparisons(
        tenant_id=principal.tenant_id,
        limit=limit,
    )
    return EvaluationComparisonListResponse(
        items=tuple(
            EvaluationComparisonSummaryResponse.model_validate(item, from_attributes=True)
            for item in comparisons
        )
    )


@router.get(
    "/comparisons/{comparison_id}",
    response_model=EvaluationComparisonResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def get_evaluation_comparison(
    comparison_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationComparisonResponse:
    """读取受权限保护的多模型对比及逐用例完整回答。"""
    try:
        comparison = await service.get_comparison(
            comparison_id=comparison_id,
            tenant_id=principal.tenant_id,
        )
    except EvaluationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return EvaluationComparisonResponse.model_validate(comparison, from_attributes=True)


@router.post(
    "/blind-assignments",
    response_model=BlindReviewAssignmentResponse | None,
    dependencies=[Depends(require_permission(AdminPermission.EVALUATION_REVIEW))],
)
async def claim_blind_review(
    command: BlindReviewAssignmentCreate,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> BlindReviewAssignmentResponse | None:
    """领取稳定的随机 A/B 任务；响应故意省略运行、模型和来源字段。"""
    item = await service.claim_blind_assignment(
        tenant_id=principal.tenant_id,
        reviewer_id=principal.user_id,
        run_id=command.run_id,
    )
    if item is None:
        return None
    return BlindReviewAssignmentResponse.model_validate(item, from_attributes=True)


@router.post(
    "/blind-assignments/{assignment_id}/reviews",
    response_model=BlindReviewResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.EVALUATION_REVIEW))],
)
async def submit_blind_review(
    assignment_id: UUID,
    command: BlindReviewSubmit,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> BlindReviewResponse:
    """提交双侧独立评分，由服务端映射并保存候选/参考结论。"""
    try:
        review = await service.submit_blind_review(
            assignment_id=assignment_id,
            tenant_id=principal.tenant_id,
            reviewer_id=principal.user_id,
            displayed_preference=command.preference,
            response_a_score=BlindReviewScore(**command.response_a_score.model_dump()),
            response_b_score=BlindReviewScore(**command.response_b_score.model_dump()),
            note=command.note,
        )
    except EvaluationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except EvaluationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except EvaluationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return BlindReviewResponse.model_validate(review, from_attributes=True)


@router.get(
    "/report",
    response_model=EvaluationReportResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_READ))],
)
async def get_evaluation_report(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[EvaluationService, Depends(get_evaluation_service)],
) -> EvaluationReportResponse:
    """聚合自动门禁和团队盲评结果，并给出当前评审剩余数量。"""
    report = await service.get_report(
        tenant_id=principal.tenant_id,
        reviewer_id=principal.user_id,
    )
    return EvaluationReportResponse.model_validate(report, from_attributes=True)
