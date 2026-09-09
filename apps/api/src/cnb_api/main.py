"""FastAPI 应用组合根。"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import NAMESPACE_DNS, UUID, uuid5

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cnb_api import __version__
from cnb_api.errors import RequestIdMiddleware, install_error_handlers
from cnb_api.routes import (
    administration,
    attachment,
    cognition,
    configuration,
    conversation,
    health,
    memory,
    system,
)
from cnb_application import (
    AdministrationRepository,
    AttachmentRepository,
    CognitionRepository,
    ConfigurationRepository,
    ConversationRepository,
    MemoryRepository,
    ModelProviderResolver,
    ModelReliabilityGuard,
    ObjectStorage,
    SecretStore,
    StaticModelProviderResolver,
)
from cnb_cognition import AnthropomorphicCognitiveRuntime, CognitiveRuntime, ModelProvider
from cnb_domain import DevelopmentIdentity
from cnb_infrastructure import (
    AesGcmEnvelopeCipher,
    ConfiguredModelProviderResolver,
    DependencyProbe,
    InMemoryMemoryRepository,
    MemoryAdministrationRepository,
    MemoryAttachmentRepository,
    MemoryCognitionRepository,
    MemoryObjectStorage,
    MemorySecretStore,
    MinioObjectStorage,
    Settings,
    SqlAlchemyAdministrationRepository,
    SqlAlchemyAttachmentRepository,
    SqlAlchemyCognitionRepository,
    SqlAlchemyConfigurationRepository,
    SqlAlchemyConversationRepository,
    SqlAlchemyMemoryRepository,
    SqlAlchemySecretStore,
    get_settings,
    probe_dependencies,
)
from cnb_infrastructure.database import create_session_factory


def create_app(
    settings: Settings | None = None,
    *,
    configuration_repository: ConfigurationRepository | None = None,
    cognition_repository: CognitionRepository | None = None,
    conversation_repository: ConversationRepository | None = None,
    memory_repository: MemoryRepository | None = None,
    administration_repository: AdministrationRepository | None = None,
    attachment_repository: AttachmentRepository | None = None,
    object_storage: ObjectStorage | None = None,
    secret_store: SecretStore | None = None,
    cognitive_runtime: CognitiveRuntime | None = None,
    model_provider: ModelProvider | None = None,
    model_provider_resolver: ModelProviderResolver | None = None,
    dependency_probe: DependencyProbe | None = None,
) -> FastAPI:
    """创建可用于生产或测试的独立应用实例。"""
    resolved_settings = settings or get_settings()

    agent_run_tasks: dict[UUID, asyncio.Task[None]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            for task in agent_run_tasks.values():
                task.cancel()
            if agent_run_tasks:
                await asyncio.gather(*agent_run_tasks.values(), return_exceptions=True)

    application = FastAPI(
        title="Cyber Netizen Bot API",
        summary="赛博网友管理与消息网关",
        version=__version__,
        docs_url="/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
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
    session_factory = create_session_factory(resolved_settings)
    application.state.configuration_repository = (
        configuration_repository or SqlAlchemyConfigurationRepository(session_factory)
    )
    application.state.conversation_repository = (
        conversation_repository or SqlAlchemyConversationRepository(session_factory)
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
            development_identity
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
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID"],
    )
    application.include_router(health.router)
    application.include_router(system.router, prefix="/api/v1")
    application.include_router(administration.router, prefix="/api/v1")
    application.include_router(configuration.router, prefix="/api/v1")
    application.include_router(cognition.router, prefix="/api/v1")
    application.include_router(memory.router, prefix="/api/v1")
    application.include_router(attachment.router, prefix="/api/v1")
    application.include_router(conversation.router, prefix="/api/v1")
    return application


app = create_app()
