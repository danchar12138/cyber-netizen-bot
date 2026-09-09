"""FastAPI 依赖提供器与进程内单例。"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

from cnb_application import (
    ConfigurationRegistry,
    ConfigurationRepository,
    ConfigurationService,
    build_default_registry,
)


@lru_cache(maxsize=1)
def get_configuration_registry() -> ConfigurationRegistry:
    """返回不可变的内置配置注册表。"""
    return build_default_registry()


def get_configuration_repository(request: Request) -> ConfigurationRepository:
    """返回组合根为当前应用选择的配置仓储。"""
    repository: ConfigurationRepository = request.app.state.configuration_repository
    return repository


def get_configuration_service(
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
    repository: Annotated[ConfigurationRepository, Depends(get_configuration_repository)],
) -> ConfigurationService:
    """使用进程级端口构建请求级配置服务。"""
    return ConfigurationService(registry, repository)
