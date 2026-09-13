"""长期记忆、关系、Episode 与索引治理路由。"""

from collections.abc import Mapping
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from cnb_api.dependencies import (
    get_admin_principal,
    get_configuration_service,
    get_memory_service,
    get_request_identity,
    get_task_service,
    require_permission,
)
from cnb_application import (
    BackgroundTaskService,
    ConfigurationService,
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryService,
    MemorySourceDraft,
    MemoryValidationError,
)
from cnb_cognition import HybridRecallWeights
from cnb_contracts import (
    EpisodeCloseCommand,
    EpisodeCreate,
    EpisodeListResponse,
    EpisodeResponse,
    MemoryConfirmationCommand,
    MemoryConflictCommand,
    MemoryCorrectionCommand,
    MemoryCreate,
    MemoryDetailResponse,
    MemoryForgetCommand,
    MemoryIndexJobListResponse,
    MemoryIndexJobResponse,
    MemoryIndexRebuildCommand,
    MemoryLinkResponse,
    MemoryListResponse,
    MemoryRecallCommand,
    MemoryRecallItemResponse,
    MemoryRecallResponse,
    MemoryResponse,
    MemorySourceResponse,
    RelationshipDetailResponse,
    RelationshipEventCreate,
    RelationshipEventResponse,
    RelationshipResponse,
)
from cnb_domain import (
    AdminPermission,
    AdminPrincipal,
    BackgroundJobKind,
    JsonValue,
    MemoryDetail,
    MemoryKind,
    MemorySensitivity,
    MemoryStatus,
    RelationshipDetail,
)

router = APIRouter(
    prefix="/memory",
    tags=["memory"],
    dependencies=[
        Depends(require_permission(AdminPermission.MEMORY_READ)),
        Depends(get_request_identity),
    ],
)


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    user_id: UUID | None = None,
    memory_status: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    kind: MemoryKind | None = None,
    query: str | None = Query(default=None, max_length=8000),
    limit: int = Query(default=100, ge=1, le=200),
) -> MemoryListResponse:
    """按当前租户和智能体搜索长期记忆。"""
    items = await service.list_memories(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(request),
        user_id=user_id,
        status=memory_status,
        kind=kind,
        query=query,
        limit=limit,
    )
    return MemoryListResponse(
        items=tuple(MemoryResponse.model_validate(item, from_attributes=True) for item in items)
    )


@router.post(
    "/memories",
    response_model=MemoryDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def create_memory(
    command: MemoryCreate,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> MemoryDetailResponse:
    """创建具有显式来源和治理属性的长期记忆。"""
    try:
        detail = await service.create_memory(
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            user_id=command.user_id or principal.user_id,
            conversation_id=command.conversation_id,
            episode_id=command.episode_id,
            kind=command.kind,
            visibility=command.visibility,
            content=command.content,
            event_at=command.event_at,
            confidence=command.confidence,
            importance=command.importance,
            emotional_weight=command.emotional_weight,
            sensitivity=command.sensitivity,
            confirmation=command.confirmation,
            sources=tuple(
                MemorySourceDraft(
                    kind=item.kind,
                    source_id=item.source_id,
                    excerpt=item.excerpt,
                    is_verbatim=item.is_verbatim,
                    occurred_at=item.occurred_at,
                )
                for item in command.sources
            ),
            actor_id=principal.user_id,
        )
    except MemoryValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _memory_detail(detail)


@router.get("/memories/{memory_id}", response_model=MemoryDetailResponse)
async def get_memory(
    memory_id: UUID,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> MemoryDetailResponse:
    """查看一条记忆及其来源、冲突和替代链。"""
    try:
        detail = await service.get_memory_detail(
            memory_id=memory_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _memory_detail(detail)


@router.post(
    "/memories/{memory_id}/confirmation",
    response_model=MemoryResponse,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def set_memory_confirmation(
    memory_id: UUID,
    command: MemoryConfirmationCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> MemoryResponse:
    """确认记忆或标记争议，并同步调整置信度。"""
    try:
        item = await service.set_confirmation(
            memory_id=memory_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            confirmation=command.confirmation,
            actor_id=principal.user_id,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MemoryConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return MemoryResponse.model_validate(item, from_attributes=True)


@router.post(
    "/memories/{memory_id}/corrections",
    response_model=MemoryDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def correct_memory(
    memory_id: UUID,
    command: MemoryCorrectionCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> MemoryDetailResponse:
    """创建纠正版本，不覆盖或删除原始审计事实。"""
    try:
        detail = await service.correct_memory(
            memory_id=memory_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            content=command.content,
            event_at=command.event_at,
            actor_id=principal.user_id,
            note=command.note,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MemoryConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except MemoryValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _memory_detail(detail)


@router.post(
    "/memories/{memory_id}/conflicts",
    response_model=MemoryLinkResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def create_memory_conflict(
    memory_id: UUID,
    command: MemoryConflictCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> MemoryLinkResponse:
    """显式登记两条记忆的冲突，不静默覆盖其中任意一条。"""
    try:
        link = await service.link_conflict(
            source_memory_id=memory_id,
            target_memory_id=command.target_memory_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            actor_id=principal.user_id,
            note=command.note,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MemoryValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return MemoryLinkResponse.model_validate(link, from_attributes=True)


@router.post(
    "/memories/{memory_id}/forget",
    response_model=MemoryResponse,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def forget_memory(
    memory_id: UUID,
    command: MemoryForgetCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> MemoryResponse:
    """清除正文、来源摘录和向量，同时保留最小审计骨架。"""
    if not command.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="遗忘操作必须显式确认",
        )
    try:
        item = await service.forget_memory(
            memory_id=memory_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            actor_id=principal.user_id,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return MemoryResponse.model_validate(item, from_attributes=True)


@router.post("/recall", response_model=MemoryRecallResponse)
async def recall_memories(
    command: MemoryRecallCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> MemoryRecallResponse:
    """按发布配置执行全文、语义、时间、重要性和关系混合召回。"""
    agent_id = _agent_id(request)
    configuration = await configuration_service.resolve_effective(
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        user_id=command.user_id,
    )
    values = configuration.values
    try:
        items = await service.recall(
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            user_id=command.user_id,
            query=command.query,
            limit=command.limit or _integer(values, "memory.recall.limit"),
            candidate_pool=_integer(values, "memory.recall.candidate_pool"),
            maximum_sensitivity=MemorySensitivity(
                _string(values, "memory.recall.maximum_sensitivity")
            ),
            recency_half_life_days=_number(values, "memory.recall.recency_half_life_days"),
            weights=_weights(values),
        )
    except (MemoryValidationError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return MemoryRecallResponse(
        items=tuple(
            MemoryRecallItemResponse(
                memory=MemoryResponse.model_validate(item.memory, from_attributes=True),
                score=item.score,
                components=item.components,
            )
            for item in items
        ),
        embedding_version=service.embedding_version,
    )


@router.get("/episodes", response_model=EpisodeListResponse)
async def list_episodes(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    user_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> EpisodeListResponse:
    """列出当前智能体的情景记录。"""
    items = await service.list_episodes(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(request),
        user_id=user_id,
        limit=limit,
    )
    return EpisodeListResponse(
        items=tuple(EpisodeResponse.model_validate(item, from_attributes=True) for item in items)
    )


@router.post(
    "/episodes",
    response_model=EpisodeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def create_episode(
    command: EpisodeCreate,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> EpisodeResponse:
    """创建带来源消息列表的 Episode。"""
    try:
        item = await service.create_episode(
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            user_id=command.user_id,
            conversation_id=command.conversation_id,
            title=command.title,
            summary=command.summary,
            started_at=command.started_at,
            ended_at=command.ended_at,
            source_message_ids=command.source_message_ids,
            actor_id=principal.user_id,
        )
    except MemoryValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return EpisodeResponse.model_validate(item, from_attributes=True)


@router.post(
    "/episodes/{episode_id}/close",
    response_model=EpisodeResponse,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def close_episode(
    episode_id: UUID,
    command: EpisodeCloseCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> EpisodeResponse:
    """关闭 Episode 或标记为已巩固。"""
    try:
        item = await service.close_episode(
            episode_id=episode_id,
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            consolidate=command.consolidate,
            actor_id=principal.user_id,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return EpisodeResponse.model_validate(item, from_attributes=True)


@router.get("/relationship", response_model=RelationshipDetailResponse)
async def get_relationship(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    user_id: UUID,
) -> RelationshipDetailResponse:
    """查看当前智能体与指定用户的关系快照和事件。"""
    detail = await service.get_relationship(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(request),
        user_id=user_id,
    )
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="尚无关系记录")
    return _relationship_detail(detail)


@router.post(
    "/relationship/events",
    response_model=RelationshipDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_WRITE))],
)
async def create_relationship_event(
    command: RelationshipEventCreate,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> RelationshipDetailResponse:
    """追加关系事件并确定性推进关系阶段。"""
    try:
        detail = await service.record_relationship_event(
            tenant_id=principal.tenant_id,
            agent_id=_agent_id(request),
            user_id=command.user_id,
            event_type=command.event_type,
            affinity_delta=command.affinity_delta,
            trust_delta=command.trust_delta,
            familiarity_delta=command.familiarity_delta,
            summary=command.summary,
            boundaries=command.boundaries,
            evidence_memory_id=command.evidence_memory_id,
            actor_id=principal.user_id,
        )
    except MemoryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MemoryValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _relationship_detail(detail)


@router.get("/index-jobs", response_model=MemoryIndexJobListResponse)
async def list_index_jobs(
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryIndexJobListResponse:
    """查看可审计的 embedding 重建任务进度。"""
    items = await service.list_index_jobs(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(request),
        limit=limit,
    )
    return MemoryIndexJobListResponse(
        items=tuple(
            MemoryIndexJobResponse.model_validate(item, from_attributes=True) for item in items
        )
    )


@router.post(
    "/index-jobs",
    response_model=MemoryIndexJobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(AdminPermission.MEMORY_REBUILD))],
)
async def rebuild_index(
    command: MemoryIndexRebuildCommand,
    request: Request,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    tasks: Annotated[BackgroundTaskService, Depends(get_task_service)],
    configuration: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> MemoryIndexJobResponse:
    """建立索引进度和事务发件箱任务，由 Dramatiq 任务进程异步执行。"""
    if not command.confirmed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="索引重建必须显式确认",
        )
    item = await service.request_embedding_rebuild(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(request),
        user_id=command.user_id,
        actor_id=principal.user_id,
    )
    snapshot = await configuration.resolve_effective(
        tenant_id=principal.tenant_id,
        agent_id=_agent_id(request),
        user_id=command.user_id,
    )
    await tasks.enqueue(
        tenant_id=principal.tenant_id,
        kind=BackgroundJobKind.EMBEDDING_REBUILD,
        payload={
            "index_job_id": str(item.id),
            "agent_id": str(item.agent_id),
            "actor_id": str(principal.user_id),
        },
        deduplication_key=f"memory-index:{item.id}",
        created_by=principal.user_id,
        max_attempts=_integer(snapshot.values, "tasks.max_attempts"),
        lease_seconds=_integer(snapshot.values, "tasks.lease_seconds"),
        retry_base_seconds=_integer(snapshot.values, "tasks.retry_base_seconds"),
        correlation_id=str(item.id),
    )
    return MemoryIndexJobResponse.model_validate(item, from_attributes=True)


def _memory_detail(detail: MemoryDetail) -> MemoryDetailResponse:
    return MemoryDetailResponse(
        memory=MemoryResponse.model_validate(detail.memory, from_attributes=True),
        sources=tuple(
            MemorySourceResponse.model_validate(item, from_attributes=True)
            for item in detail.sources
        ),
        links=tuple(
            MemoryLinkResponse.model_validate(item, from_attributes=True) for item in detail.links
        ),
    )


def _relationship_detail(detail: RelationshipDetail) -> RelationshipDetailResponse:
    return RelationshipDetailResponse(
        relationship=RelationshipResponse.model_validate(detail.relationship, from_attributes=True),
        events=tuple(
            RelationshipEventResponse.model_validate(item, from_attributes=True)
            for item in detail.events
        ),
    )


def _agent_id(request: Request) -> UUID:
    return request.state.request_identity.agent_id


def _integer(values: Mapping[str, JsonValue], key: str) -> int:
    value = values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"生效配置中的整数参数无效：{key}")
    return value


def _number(values: Mapping[str, JsonValue], key: str) -> float:
    value = values.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"生效配置中的数值参数无效：{key}")
    return float(value)


def _string(values: Mapping[str, JsonValue], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str):
        raise ValueError(f"生效配置中的字符串参数无效：{key}")
    return value


def _weights(values: Mapping[str, JsonValue]) -> HybridRecallWeights:
    return HybridRecallWeights(
        full_text=_number(values, "memory.recall.full_text_weight"),
        semantic=_number(values, "memory.recall.semantic_weight"),
        recency=_number(values, "memory.recall.recency_weight"),
        importance=_number(values, "memory.recall.importance_weight"),
        relationship=_number(values, "memory.recall.relationship_weight"),
    )
