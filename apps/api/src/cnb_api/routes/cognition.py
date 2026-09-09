"""认知资源版本、运行轨迹与确定性评测 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from cnb_api.dependencies import get_admin_principal, get_cognition_service, require_permission
from cnb_application import (
    CognitionResourceNotFoundError,
    CognitionService,
    CognitionValidationError,
)
from cnb_contracts import (
    ActionCandidateResponse,
    CognitionPayloadTestCommand,
    CognitionPayloadTestResponse,
    CognitionResourceDraftCreate,
    CognitionResourceListResponse,
    CognitionResourceResponse,
    CognitiveRunTraceResponse,
    EvaluationCaseResponse,
    EvaluationSuiteResponse,
    ModelInvocationResponse,
    PersonaStateResponse,
    RunStepResponse,
)
from cnb_domain import AdminPermission, AdminPrincipal, CognitionResourceKind

router = APIRouter(prefix="/cognition", tags=["cognition"])


@router.get(
    "/resources",
    response_model=CognitionResourceListResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_READ))],
)
async def list_resources(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[CognitionService, Depends(get_cognition_service)],
    kind: CognitionResourceKind | None = None,
) -> CognitionResourceListResponse:
    """列出当前 Agent 的全部认知资源版本。"""
    items = await service.list_resources(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(service),
        kind=kind,
    )
    return CognitionResourceListResponse(
        items=tuple(
            CognitionResourceResponse.model_validate(item, from_attributes=True) for item in items
        )
    )


@router.post(
    "/resources",
    response_model=CognitionResourceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_WRITE))],
)
async def create_resource_draft(
    command: CognitionResourceDraftCreate,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[CognitionService, Depends(get_cognition_service)],
) -> CognitionResourceResponse:
    """校验并创建一个新的不可变认知资源草稿。"""
    try:
        item = await service.create_draft(
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(service),
            kind=command.kind,
            key=command.key,
            name=command.name,
            payload=command.payload,
            note=command.note,
            actor_id=principal.user_id,
        )
    except CognitionValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return CognitionResourceResponse.model_validate(item, from_attributes=True)


@router.post(
    "/resources/test",
    response_model=CognitionPayloadTestResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_WRITE))],
)
async def test_resource_payload(
    command: CognitionPayloadTestCommand,
) -> CognitionPayloadTestResponse:
    """在保存前执行确定性结构与安全边界测试。"""
    valid, messages = CognitionService.test_payload(command.kind, command.payload)
    return CognitionPayloadTestResponse(valid=valid, messages=messages)


@router.post(
    "/resources/{resource_id}/publish",
    response_model=CognitionResourceResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_WRITE))],
)
async def publish_resource(
    resource_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[CognitionService, Depends(get_cognition_service)],
) -> CognitionResourceResponse:
    """发布草稿，并原子替代同资源键的旧发布版本。"""
    try:
        item = await service.publish(
            resource_id=resource_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(service),
            actor_id=principal.user_id,
        )
    except CognitionResourceNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CognitionValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return CognitionResourceResponse.model_validate(item, from_attributes=True)


@router.post(
    "/resources/{resource_id}/rollback",
    response_model=CognitionResourceResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_WRITE))],
)
async def rollback_resource(
    resource_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[CognitionService, Depends(get_cognition_service)],
) -> CognitionResourceResponse:
    """从任意历史版本复制并立即发布一个新的回滚版本。"""
    try:
        item = await service.rollback(
            resource_id=resource_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(service),
            actor_id=principal.user_id,
        )
    except CognitionResourceNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except CognitionValidationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return CognitionResourceResponse.model_validate(item, from_attributes=True)


@router.get(
    "/runs/{run_id}/trace",
    response_model=CognitiveRunTraceResponse,
    dependencies=[Depends(require_permission(AdminPermission.TRACE_READ))],
)
async def get_run_trace(
    run_id: UUID,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[CognitionService, Depends(get_cognition_service)],
) -> CognitiveRunTraceResponse:
    """返回安全阶段摘要、行动候选、情绪快照和模型调用。"""
    try:
        trace = await service.get_run_trace(run_id=run_id, tenant_id=principal.tenant_id)
    except CognitionResourceNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return CognitiveRunTraceResponse(
        run_id=trace.run_id,
        persona_state=(
            PersonaStateResponse.model_validate(trace.persona_state, from_attributes=True)
            if trace.persona_state
            else None
        ),
        steps=tuple(
            RunStepResponse.model_validate(item, from_attributes=True) for item in trace.steps
        ),
        candidates=tuple(
            ActionCandidateResponse.model_validate(item, from_attributes=True)
            for item in trace.candidates
        ),
        model_invocations=tuple(
            ModelInvocationResponse.model_validate(item, from_attributes=True)
            for item in trace.model_invocations
        ),
    )


@router.post(
    "/evaluations/run",
    response_model=EvaluationSuiteResponse,
    dependencies=[Depends(require_permission(AdminPermission.COGNITION_EVALUATE))],
)
async def run_evaluation_suite(
    service: Annotated[CognitionService, Depends(get_cognition_service)],
) -> EvaluationSuiteResponse:
    """运行不产生外部调用的中文拟人行为回放集。"""
    cases = await service.run_evaluation_suite()
    return EvaluationSuiteResponse(
        passed=sum(item.passed for item in cases),
        total=len(cases),
        cases=tuple(
            EvaluationCaseResponse.model_validate(item, from_attributes=True) for item in cases
        ),
    )


def _agent_id(service: CognitionService) -> UUID:
    """从请求级服务获取组合根固定的当前 Agent。"""
    return service.agent_id
