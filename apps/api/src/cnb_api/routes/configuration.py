"""由 Schema 驱动的配置管理接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from cnb_api.dependencies import get_configuration_registry, get_configuration_service
from cnb_application import (
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    ConfigurationRegistry,
    ConfigurationService,
    ConfigurationValidationError,
)
from cnb_contracts import (
    ConfigDefinitionResponse,
    ConfigDraftCreate,
    ConfigRegistryResponse,
    ConfigValueResponse,
    ConfigVersionListResponse,
    ConfigVersionResponse,
)
from cnb_domain import ConfigEntry, ConfigVersion

router = APIRouter(prefix="/configuration", tags=["configuration"])


@router.get("/definitions", response_model=ConfigRegistryResponse)
async def list_definitions(
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
) -> ConfigRegistryResponse:
    """公开安全的 Schema 与默认值，绝不包含密钥内容。"""
    return ConfigRegistryResponse(
        definitions=tuple(
            ConfigDefinitionResponse.model_validate(definition) for definition in registry.all()
        )
    )


def _version_response(version: ConfigVersion) -> ConfigVersionResponse:
    return ConfigVersionResponse(
        id=version.id,
        version=version.version,
        status=version.status,
        note=version.note,
        created_at=version.created_at,
        published_at=version.published_at,
        values=tuple(
            ConfigValueResponse(
                key=value.key,
                scope_type=value.scope_type,
                scope_id=value.scope_id,
                value=value.value,
            )
            for value in version.values
        ),
    )


@router.get("/versions", response_model=ConfigVersionListResponse)
async def list_versions(
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ConfigVersionListResponse:
    """按从新到旧的顺序返回不可变配置历史。"""
    return ConfigVersionListResponse(
        versions=tuple(_version_response(item) for item in await service.list_versions())
    )


@router.get("/versions/{version_id}", response_model=ConfigVersionResponse)
async def get_version(
    version_id: UUID,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ConfigVersionResponse:
    """返回一个不含密钥明文的配置版本。"""
    try:
        return _version_response(await service.get_version(version_id))
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/drafts", response_model=ConfigVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_draft(
    command: ConfigDraftCreate,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ConfigVersionResponse:
    """校验并保存一个新的不可变配置草稿。"""
    entries = tuple(
        ConfigEntry(
            key=item.key,
            scope_type=item.scope_type,
            scope_id=item.scope_id,
            value=item.value,
        )
        for item in command.values
    )
    try:
        return _version_response(await service.create_draft(note=command.note, values=entries))
    except ConfigurationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except ConfigurationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/versions/{version_id}/publish", response_model=ConfigVersionResponse)
async def publish_version(
    version_id: UUID,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ConfigVersionResponse:
    """原子发布草稿，并将其设为唯一生效版本。"""
    try:
        return _version_response(await service.publish(version_id))
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConfigurationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/versions/{version_id}/rollback", response_model=ConfigVersionResponse)
async def rollback_version(
    version_id: UUID,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
) -> ConfigVersionResponse:
    """复制历史快照并发布为新的不可变版本。"""
    try:
        return _version_response(await service.rollback(version_id))
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConfigurationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
