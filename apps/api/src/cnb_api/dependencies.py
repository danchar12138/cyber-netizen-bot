"""FastAPI 依赖提供器与进程内单例。"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from starlette.requests import HTTPConnection

from cnb_application import (
    ConfigurationRegistry,
    ConfigurationRepository,
    ConfigurationService,
    ConversationRepository,
    ConversationService,
    build_default_registry,
)
from cnb_cognition import CognitiveRuntime, ModelProvider


@lru_cache(maxsize=1)
def get_configuration_registry() -> ConfigurationRegistry:
    """返回不可变的内置配置注册表。"""
    return build_default_registry()


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


def get_conversation_repository(request: HTTPConnection) -> ConversationRepository:
    """返回组合根为当前应用选择的对话仓储。"""
    repository: ConversationRepository = request.app.state.conversation_repository
    return repository


def get_conversation_service(
    request: HTTPConnection,
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    configuration_service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ConversationService:
    """使用进程级端口和开发身份构建请求级对话服务。"""
    runtime: CognitiveRuntime = request.app.state.cognitive_runtime
    model_provider: ModelProvider = request.app.state.model_provider
    return ConversationService(
        repository=repository,
        runtime=runtime,
        model_provider=model_provider,
        configuration_service=configuration_service,
        identity=request.app.state.development_identity,
    )
