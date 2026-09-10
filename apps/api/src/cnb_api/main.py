"""FastAPI 应用组合根。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cnb_adapters import ChannelAdapterRegistry, build_default_channel_registry
from cnb_api import __version__
from cnb_api.errors import RequestIdMiddleware, SecurityHeadersMiddleware, install_error_handlers
from cnb_api.openapi import stable_operation_id
from cnb_api.routes import (
    administration,
    attachment,
    authentication,
    channels,
    cognition,
    configuration,
    conversation,
    data_lifecycle,
    evaluations,
    health,
    inbound,
    memory,
    observability,
    system,
    tasks,
)
from cnb_api.telemetry import SafeObservabilityMiddleware, configure_telemetry
from cnb_application import (
    AdminAuthenticator,
    AdministrationRepository,
    AttachmentRepository,
    ChannelRepository,
    CognitionRepository,
    ConfigurationRepository,
    ConversationRepository,
    DataLifecycleRepository,
    EvaluationRepository,
    InboundGatewayRepository,
    MemoryRepository,
    ModelProviderResolver,
    ModelReliabilityGuard,
    ObjectStorage,
    ObservabilityRepository,
    SecretStore,
    StaticModelProviderResolver,
    TaskRepository,
)
from cnb_cognition import AnthropomorphicCognitiveRuntime, CognitiveRuntime, ModelProvider
from cnb_contracts import ApiErrorResponse
from cnb_domain import DevelopmentIdentity
from cnb_infrastructure import (
    AesGcmEnvelopeCipher,
    ConfiguredModelProviderResolver,
    DependencyProbe,
    InMemoryMemoryRepository,
    InMemoryTaskRepository,
    MemoryAdministrationRepository,
    MemoryAttachmentRepository,
    MemoryChannelRepository,
    MemoryCognitionRepository,
    MemoryDataLifecycleRepository,
    MemoryEvaluationRepository,
    MemoryInboundGatewayRepository,
    MemoryObjectStorage,
    MemoryObservabilityRepository,
    MemorySecretStore,
    MinioObjectStorage,
    OidcAuthenticator,
    Settings,
    SqlAlchemyAdministrationRepository,
    SqlAlchemyAttachmentRepository,
    SqlAlchemyChannelRepository,
    SqlAlchemyCognitionRepository,
    SqlAlchemyConfigurationRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyDataLifecycleRepository,
    SqlAlchemyEvaluationRepository,
    SqlAlchemyInboundGatewayRepository,
    SqlAlchemyMemoryRepository,
    SqlAlchemyObservabilityRepository,
    SqlAlchemySecretStore,
    SqlAlchemyTaskRepository,
    get_settings,
    probe_dependencies,
)
from cnb_infrastructure.database import create_session_factory


def create_app(
    settings: Settings | None = None,
    *,
    configuration_repository: ConfigurationRepository | None = None,
    cognition_repository: CognitionRepository | None = None,
    channel_repository: ChannelRepository | None = None,
    channel_adapter_registry: ChannelAdapterRegistry | None = None,
    conversation_repository: ConversationRepository | None = None,
    data_lifecycle_repository: DataLifecycleRepository | None = None,
    evaluation_repository: EvaluationRepository | None = None,
    inbound_repository: InboundGatewayRepository | None = None,
    memory_repository: MemoryRepository | None = None,
    task_repository: TaskRepository | None = None,
    administration_repository: AdministrationRepository | None = None,
    attachment_repository: AttachmentRepository | None = None,
    object_storage: ObjectStorage | None = None,
    observability_repository: ObservabilityRepository | None = None,
    secret_store: SecretStore | None = None,
    cognitive_runtime: CognitiveRuntime | None = None,
    model_provider: ModelProvider | None = None,
    model_provider_resolver: ModelProviderResolver | None = None,
    dependency_probe: DependencyProbe | None = None,
    admin_authenticator: AdminAuthenticator | None = None,
) -> FastAPI:
    """创建可用于生产或测试的独立应用实例。"""
    resolved_settings = settings or get_settings()
    telemetry_runtime = configure_telemetry(resolved_settings)

    agent_run_tasks: dict[UUID, asyncio.Task[Any]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            for task in agent_run_tasks.values():
                task.cancel()
            if agent_run_tasks:
                await asyncio.gather(*agent_run_tasks.values(), return_exceptions=True)
            await application.state.channel_adapter_registry.aclose()
            telemetry_runtime.shutdown()

    error_responses: dict[int | str, dict[str, Any]] = {
        status_code: {"model": ApiErrorResponse, "description": description}
        for status_code, description in {
            400: "请求格式或业务条件无效",
            401: "尚未通过身份认证",
            403: "当前身份没有所需权限",
            404: "请求的资源不存在",
            409: "资源状态或幂等约束冲突",
            422: "请求字段校验失败",
            429: "请求超过允许频率",
            500: "服务发生已安全处理的内部错误",
            503: "依赖服务暂时不可用",
        }.items()
    }
    application = FastAPI(
        title="Cyber Netizen Bot API",
        summary="赛博网友管理与消息网关",
        version=__version__,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
        generate_unique_id_function=stable_operation_id,
        responses=error_responses,
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    development_identity = DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.user"),
        agent_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.agent"),
        user_name="本地开发者",
        agent_name="赛博网友",
    )
    application.state.development_identity = development_identity
    administration_seed_identity = (
        DevelopmentIdentity(
            tenant_id=resolved_settings.oidc_tenant_id,
            user_id=development_identity.user_id,
            agent_id=resolved_settings.oidc_agent_id,
            user_name=development_identity.user_name,
            agent_name=resolved_settings.oidc_agent_name,
        )
        if resolved_settings.authentication_mode == "oidc"
        and resolved_settings.oidc_tenant_id is not None
        and resolved_settings.oidc_agent_id is not None
        else development_identity
    )
    session_factory = create_session_factory(resolved_settings)
    application.state.admin_authenticator = (
        admin_authenticator
        if admin_authenticator is not None
        else OidcAuthenticator(resolved_settings, session_factory)
        if resolved_settings.authentication_mode == "oidc"
        else None
    )
    application.state.configuration_repository = (
        configuration_repository or SqlAlchemyConfigurationRepository(session_factory)
    )
    if channel_repository is not None:
        application.state.channel_repository = channel_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.channel_repository = MemoryChannelRepository()
    else:
        application.state.channel_repository = SqlAlchemyChannelRepository(session_factory)
    application.state.channel_adapter_registry = (
        channel_adapter_registry or build_default_channel_registry()
    )
    application.state.conversation_repository = (
        conversation_repository or SqlAlchemyConversationRepository(session_factory)
    )
    if inbound_repository is not None:
        application.state.inbound_repository = inbound_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.inbound_repository = MemoryInboundGatewayRepository(
            administration_seed_identity
        )
    else:
        application.state.inbound_repository = SqlAlchemyInboundGatewayRepository(session_factory)
    if data_lifecycle_repository is not None:
        application.state.data_lifecycle_repository = data_lifecycle_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.data_lifecycle_repository = MemoryDataLifecycleRepository(
            development_identity
        )
    else:
        application.state.data_lifecycle_repository = SqlAlchemyDataLifecycleRepository(
            session_factory
        )
    if memory_repository is not None:
        application.state.memory_repository = memory_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.memory_repository = InMemoryMemoryRepository()
    else:
        application.state.memory_repository = SqlAlchemyMemoryRepository(session_factory)
    if cognition_repository is not None:
        application.state.cognition_repository = cognition_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.cognition_repository = MemoryCognitionRepository()
    else:
        application.state.cognition_repository = SqlAlchemyCognitionRepository(session_factory)
    if evaluation_repository is not None:
        application.state.evaluation_repository = evaluation_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.evaluation_repository = MemoryEvaluationRepository()
    else:
        application.state.evaluation_repository = SqlAlchemyEvaluationRepository(session_factory)
    if task_repository is not None:
        application.state.task_repository = task_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.task_repository = InMemoryTaskRepository()
    else:
        application.state.task_repository = SqlAlchemyTaskRepository(session_factory)
    if observability_repository is not None:
        application.state.observability_repository = observability_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.observability_repository = MemoryObservabilityRepository()
    else:
        application.state.observability_repository = SqlAlchemyObservabilityRepository(
            session_factory
        )
    if attachment_repository is not None:
        application.state.attachment_repository = attachment_repository
    elif conversation_repository is not None:
        application.state.attachment_repository = MemoryAttachmentRepository()
    else:
        application.state.attachment_repository = SqlAlchemyAttachmentRepository(session_factory)
    if object_storage is not None:
        application.state.object_storage = object_storage
    elif conversation_repository is not None:
        application.state.object_storage = MemoryObjectStorage()
    else:
        application.state.object_storage = MinioObjectStorage(resolved_settings)
    if administration_repository is not None:
        application.state.administration_repository = administration_repository
    elif configuration_repository is not None or conversation_repository is not None:
        application.state.administration_repository = MemoryAdministrationRepository(
            administration_seed_identity,
            cognition_cloner=(
                application.state.cognition_repository
                if isinstance(application.state.cognition_repository, MemoryCognitionRepository)
                else None
            ),
        )
    else:
        application.state.administration_repository = SqlAlchemyAdministrationRepository(
            session_factory
        )
    if secret_store is not None:
        application.state.secret_store = secret_store
    elif configuration_repository is not None:
        application.state.secret_store = MemorySecretStore()
    else:
        cipher = AesGcmEnvelopeCipher.from_encoded_key(
            resolved_settings.config_master_key.get_secret_value(),
            allow_development_placeholder=resolved_settings.environment in {"development", "test"},
        )
        application.state.secret_store = SqlAlchemySecretStore(session_factory, cipher)
    application.state.cognitive_runtime = cognitive_runtime or AnthropomorphicCognitiveRuntime()
    application.state.model_reliability_guard = ModelReliabilityGuard()
    if model_provider_resolver is not None:
        application.state.model_provider_resolver = model_provider_resolver
    elif model_provider is not None:
        application.state.model_provider_resolver = StaticModelProviderResolver(model_provider)
    else:
        application.state.model_provider_resolver = ConfiguredModelProviderResolver(
            application.state.secret_store
        )
    application.state.agent_run_tasks = agent_run_tasks
    install_error_handlers(application)
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    application.add_middleware(
        SafeObservabilityMiddleware,
        repository=application.state.observability_repository,
        tracer=telemetry_runtime.tracer,
    )
    application.state.dependency_probe = dependency_probe or probe_dependencies
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-CNB-Development-Role",
            "X-CNB-Agent-ID",
            "X-Request-ID",
        ],
        expose_headers=[
            "Retry-After",
            "X-Request-ID",
            "X-Content-SHA256",
            "X-Export-Run-ID",
        ],
    )
    application.include_router(health.router)
    application.include_router(authentication.router, prefix="/api/v1")
    application.include_router(system.router, prefix="/api/v1")
    application.include_router(administration.router, prefix="/api/v1")
    application.include_router(configuration.router, prefix="/api/v1")
    application.include_router(cognition.router, prefix="/api/v1")
    application.include_router(evaluations.router, prefix="/api/v1")
    application.include_router(channels.router, prefix="/api/v1")
    application.include_router(inbound.router, prefix="/api/v1")
    application.include_router(memory.router, prefix="/api/v1")
    application.include_router(observability.router, prefix="/api/v1")
    application.include_router(tasks.router, prefix="/api/v1")
    application.include_router(attachment.router, prefix="/api/v1")
    application.include_router(conversation.router, prefix="/api/v1")
    application.include_router(data_lifecycle.router, prefix="/api/v1")
    return application


app = create_app()
