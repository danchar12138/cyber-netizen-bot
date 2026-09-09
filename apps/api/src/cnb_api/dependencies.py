"""FastAPI 依赖提供器与进程内单例。"""

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from starlette.requests import HTTPConnection

from cnb_adapters import ChannelAdapterRegistry
from cnb_application import (
    AdminAuthenticator,
    AdministrationRepository,
    AdministrationService,
    AttachmentRepository,
    AttachmentService,
    AuthenticationError,
    BackgroundTaskService,
    ChannelRepository,
    ChannelService,
    CognitionRepository,
    CognitionService,
    ConfigurationRegistry,
    ConfigurationRepository,
    ConfigurationService,
    ConversationRepository,
    ConversationService,
    DataLifecycleRepository,
    DataLifecycleService,
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
from cnb_domain import AdminPermission, AdminPrincipal, AdminRole, DevelopmentIdentity


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


def get_channel_repository(request: HTTPConnection) -> ChannelRepository:
    """返回组合根选择的渠道实例与诊断仓储。"""
    repository: ChannelRepository = request.app.state.channel_repository
    return repository


def get_channel_registry(request: HTTPConnection) -> ChannelAdapterRegistry:
    """返回进程级 Channel Adapter 注册表。"""
    registry: ChannelAdapterRegistry = request.app.state.channel_adapter_registry
    return registry


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


def get_secret_store(request: HTTPConnection) -> SecretStore:
    """返回组合根选择的密钥安全存储。"""
    store: SecretStore = request.app.state.secret_store
    return store


def get_channel_service(
    repository: Annotated[ChannelRepository, Depends(get_channel_repository)],
    registry: Annotated[ChannelAdapterRegistry, Depends(get_channel_registry)],
    secret_store: Annotated[SecretStore, Depends(get_secret_store)],
) -> ChannelService:
    """构建请求级渠道控制平面服务。"""
    return ChannelService(repository, registry, secret_store)


def get_secret_management_service(
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
    store: Annotated[SecretStore, Depends(get_secret_store)],
) -> SecretManagementService:
    """构建不会向管理接口回显明文的密钥服务。"""
    return SecretManagementService(registry, store)


async def get_admin_principal(request: HTTPConnection) -> AdminPrincipal:
    """开发模式显式使用本地身份；OIDC 模式只接受已验证 Bearer JWT。"""
    settings = request.app.state.settings
    if settings.authentication_mode == "oidc":
        token = _bearer_token(request)
        authenticator: AdminAuthenticator | None = request.app.state.admin_authenticator
        if authenticator is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OIDC 认证服务尚未就绪",
            )
        try:
            return await authenticator.authenticate(token)
        except AuthenticationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(error),
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
    if settings.environment not in {"development", "test"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="当前环境禁止开发身份认证",
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
) -> Callable[..., Awaitable[AdminPrincipal]]:
    """创建由 FastAPI 注入的服务端权限守卫。"""

    async def enforce(
        principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
    ) -> AdminPrincipal:
        try:
            return require_admin_permission(principal, permission)
        except PermissionError as error:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    return enforce


def _bearer_token(request: HTTPConnection) -> str:
    authorization = request.headers.get("Authorization")
    if authorization is not None:
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.casefold() == "bearer" and token.strip():
            return token.strip()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization 必须使用 Bearer 访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    protocols = tuple(
        item.strip()
        for item in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
        if item.strip()
    )
    if "cnb.bearer" in protocols:
        index = protocols.index("cnb.bearer")
        if index + 1 < len(protocols):
            return protocols[index + 1]
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="缺少 Bearer 访问令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_request_identity(
    request: HTTPConnection,
    principal: Annotated[AdminPrincipal, Depends(get_admin_principal)],
) -> DevelopmentIdentity:
    """把开发或 OIDC 管理主体映射到当前固定 Agent 的请求身份。"""
    settings = request.app.state.settings
    if principal.authentication_mode == "development":
        identity: DevelopmentIdentity = request.app.state.development_identity
        return identity
    agent_id = settings.oidc_agent_id
    if agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC Agent 映射尚未配置",
        )
    return DevelopmentIdentity(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        agent_id=agent_id,
        user_name=principal.display_name,
        agent_name=settings.oidc_agent_name,
    )


async def get_cognition_service(
    repository: Annotated[CognitionRepository, Depends(get_cognition_repository)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
) -> CognitionService:
    """构建绑定当前认证主体所选 Agent 的请求级认知服务。"""
    return CognitionService(repository, agent_id=identity.agent_id)


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


def get_data_lifecycle_repository(request: HTTPConnection) -> DataLifecycleRepository:
    """返回组合根选择的数据生命周期真相源。"""
    repository: DataLifecycleRepository = request.app.state.data_lifecycle_repository
    return repository


def get_data_lifecycle_service(
    repository: Annotated[DataLifecycleRepository, Depends(get_data_lifecycle_repository)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
) -> DataLifecycleService:
    """构建绑定当前租户、用户和 Agent 的数据生命周期服务。"""
    return DataLifecycleService(repository, object_storage, configuration_service, identity)


def get_attachment_service(
    request: HTTPConnection,
    repository: Annotated[AttachmentRepository, Depends(get_attachment_repository)],
    object_storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
) -> AttachmentService:
    """构建请求级附件生命周期服务。"""
    return AttachmentService(
        repository=repository,
        object_storage=object_storage,
        conversation_repository=conversation_repository,
        configuration_service=configuration_service,
        identity=identity,
    )


def get_conversation_service(
    request: HTTPConnection,
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    cognition_service: Annotated[CognitionService, Depends(get_cognition_service)],
    memory_service: Annotated[MemoryService, Depends(get_memory_service)],
    task_service: Annotated[BackgroundTaskService, Depends(get_task_service)],
    identity: Annotated[DevelopmentIdentity, Depends(get_request_identity)],
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
        identity=identity,
        reliability_guard=request.app.state.model_reliability_guard,
    )
