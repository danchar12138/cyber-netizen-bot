"""FastAPI 依赖提供器与进程内单例。"""

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection

from cnb_application import (
    AdministrationRepository,
    AdministrationService,
    AttachmentRepository,
    AttachmentService,
    BackgroundTaskService,
    CognitionRepository,
    CognitionService,
    ConfigurationRegistry,
    ConfigurationRepository,
    ConfigurationService,
    ConversationRepository,
    ConversationService,
    MemoryRepository,
    MemoryService,
    ModelProviderResolver,
    ObjectStorage,
    ScheduledActionService,
    SecretManagementService,
    SecretStore,
    TaskRepository,
    build_default_registry,
    permissions_for_role,
    require_admin_permission,
)
from cnb_cognition import CognitiveRuntime
from cnb_domain import AdminPermission, AdminPrincipal, AdminRole


@lru_cache(maxsize=1)
def get_configuration_registry() -> ConfigurationRegistry:
    """返回不可变的内置配置注册表。"""
    return build_default_registry()


def get_administration_repository(request: HTTPConnection) -> AdministrationRepository:
    """返回组合根选择的管理资源仓储。"""
    repository: AdministrationRepository = request.app.state.administration_repository
    return repository


def get_administration_service(
    repository: Annotated[AdministrationRepository, Depends(get_administration_repository)],
) -> AdministrationService:
    """构建请求级管理资源应用服务。"""
    return AdministrationService(repository)


def get_configuration_repository(request: HTTPConnection) -> ConfigurationRepository:
    """返回组合根为当前应用选择的配置仓储。"""
    repository: ConfigurationRepository = request.app.state.configuration_repository
    return repository


def get_configuration_service(
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
    repository: Annotated[ConfigurationRepository, Depends(get_configuration_repository)],
) -> ConfigurationService:
    """使用进程级端口构建请求级配置服务。"""
    return ConfigurationService(registry, repository)


def get_cognition_repository(request: HTTPConnection) -> CognitionRepository:
    """返回组合根选择的认知资源与运行回放仓储。"""
    repository: CognitionRepository = request.app.state.cognition_repository
    return repository


def get_cognition_service(
    request: HTTPConnection,
    repository: Annotated[CognitionRepository, Depends(get_cognition_repository)],
) -> CognitionService:
    """构建绑定当前开发 Agent 的请求级认知服务。"""
    return CognitionService(repository, agent_id=request.app.state.development_identity.agent_id)


def get_secret_store(request: HTTPConnection) -> SecretStore:
    """返回组合根选择的密钥安全存储。"""
    store: SecretStore = request.app.state.secret_store
    return store


def get_secret_management_service(
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
    store: Annotated[SecretStore, Depends(get_secret_store)],
) -> SecretManagementService:
    """构建不会向管理接口回显明文的密钥服务。"""
    return SecretManagementService(registry, store)


def get_admin_principal(request: HTTPConnection) -> AdminPrincipal:
    """解析开发期管理主体；非开发环境等待 P7 正式认证，不静默放行。"""
    settings = request.app.state.settings
    if settings.environment not in {"development", "test"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="正式管理身份认证尚未配置",
        )
    role_value = request.headers.get("X-CNB-Development-Role", AdminRole.ADMIN.value)
    try:
        role = AdminRole(role_value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"未知开发角色：{role_value}",
        ) from error
    identity = request.app.state.development_identity
    return AdminPrincipal(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        display_name=identity.user_name,
        role=role,
        permissions=permissions_for_role(role),
        authentication_mode="development",
    )


def require_permission(
    permission: AdminPermission,
) -> Callable[[HTTPConnection], AdminPrincipal]:
    """创建由 FastAPI 注入的服务端权限守卫。"""

    def enforce(request: HTTPConnection) -> AdminPrincipal:
        principal = get_admin_principal(request)
        try:
            return require_admin_permission(principal, permission)
        except PermissionError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    return enforce


def get_current_actor_id(
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
) -> UUID:
    """返回当前已解析管理主体的稳定操作者 ID，供审计记录使用。"""
    return principal.user_id


def get_conversation_repository(request: HTTPConnection) -> ConversationRepository:
    """返回组合根为当前应用选择的对话仓储。"""
    repository: ConversationRepository = request.app.state.conversation_repository
    return repository


def get_memory_repository(request: HTTPConnection) -> MemoryRepository:
    """返回组合根选择的长期记忆与关系仓储。"""
    repository: MemoryRepository = request.app.state.memory_repository
    return repository


def get_memory_service(
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
) -> MemoryService:
    """构建请求级长期记忆与关系治理服务。"""
    return MemoryService(repository)


def get_task_repository(request: HTTPConnection) -> TaskRepository:
    """返回组合根选择的异步任务真相仓储。"""
    repository: TaskRepository = request.app.state.task_repository
    return repository


def get_task_service(
    repository: Annotated[TaskRepository, Depends(get_task_repository)],
) -> BackgroundTaskService:
    """构建请求级后台任务治理服务。"""
    return BackgroundTaskService(repository)


def get_scheduled_action_service(
    repository: Annotated[TaskRepository, Depends(get_task_repository)],
    task_service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> ScheduledActionService:
    """构建请求级主动行为调度服务。"""
    return ScheduledActionService(repository, task_service)


def get_attachment_repository(request: HTTPConnection) -> AttachmentRepository:
    """返回组合根选择的附件元数据仓储。"""
    repository: AttachmentRepository = request.app.state.attachment_repository
    return repository


def get_object_storage(request: HTTPConnection) -> ObjectStorage:
    """返回组合根选择的 MinIO 对象存储。"""
    storage: ObjectStorage = request.app.state.object_storage
    return storage


def get_attachment_service(
    request: HTTPConnection,
    repository: Annotated[AttachmentRepository, Depends(get_attachment_repository)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> AttachmentService:
    """构建请求级附件生命周期服务。"""
    return AttachmentService(
        repository=repository,
        object_storage=object_storage,
        conversation_repository=conversation_repository,
        configuration_service=configuration_service,
        identity=request.app.state.development_identity,
    )


def get_conversation_service(
    request: HTTPConnection,
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    cognition_service: Annotated[CognitionService, Depends(get_cognition_service)],
    memory_service: Annotated[MemoryService, Depends(get_memory_service)],
    task_service: Annotated[BackgroundTaskService, Depends(get_task_service)],
) -> ConversationService:
    """使用进程级端口和开发身份构建请求级对话服务。"""
    runtime: CognitiveRuntime = request.app.state.cognitive_runtime
    model_provider_resolver: ModelProviderResolver = request.app.state.model_provider_resolver
    return ConversationService(
        repository=repository,
        runtime=runtime,
        model_provider_resolver=model_provider_resolver,
        configuration_service=configuration_service,
        cognition_service=cognition_service,
        memory_service=memory_service,
        task_service=task_service,
        identity=request.app.state.development_identity,
        reliability_guard=request.app.state.model_reliability_guard,
    )
