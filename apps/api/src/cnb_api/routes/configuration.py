"""由 Schema 驱动的配置管理接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from cnb_api.dependencies import (
    get_configuration_registry,
    get_configuration_service,
    get_current_actor_id,
    get_secret_management_service,
)
from cnb_application import (
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    ConfigurationRegistry,
    ConfigurationService,
    ConfigurationValidationError,
    SecretManagementService,
    SecretNotFoundError,
    SecretOperationError,
)
from cnb_contracts import (
    ConfigDefinitionResponse,
    ConfigDifferenceResponse,
    ConfigDiffResponse,
    ConfigDraftCreate,
    ConfigRegistryResponse,
    ConfigValueResponse,
    ConfigVersionListResponse,
    ConfigVersionResponse,
    EffectiveConfigSourceResponse,
    EffectiveConfigurationResponse,
    EffectiveConfigValueResponse,
    SecretListResponse,
    SecretMetadataResponse,
    SecretRotateCommand,
    SecretWriteCommand,
)
from cnb_domain import ConfigEntry, ConfigVersion, SecretMetadata

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


@router.get("/versions/{version_id}/diff", response_model=ConfigDiffResponse)
async def preview_version_diff(
    version_id: UUID,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    base_version_id: Annotated[UUID | None, Query()] = None,
) -> ConfigDiffResponse:
    """返回相对当前发布版本或指定基线的安全差异。"""
    try:
        preview = await service.preview_diff(version_id, base_version_id=base_version_id)
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return ConfigDiffResponse(
        base_version=preview.base_version,
        target_version=preview.target_version,
        changes=tuple(
            ConfigDifferenceResponse(
                key=item.key,
                scope_type=item.scope_type,
                scope_id=item.scope_id,
                kind=item.kind,
                before=item.before,
                after=item.after,
            )
            for item in preview.changes
        ),
    )


@router.get("/effective", response_model=EffectiveConfigurationResponse)
async def get_effective_configuration(
    tenant_id: UUID,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    agent_id: UUID | None = None,
    channel_id: UUID | None = None,
    user_id: UUID | None = None,
    version: Annotated[int | None, Query(ge=0)] = None,
) -> EffectiveConfigurationResponse:
    """返回指定上下文中的最终值及逐项来源。"""
    try:
        snapshot = await service.resolve_effective(
            tenant_id=tenant_id,
            agent_id=agent_id,
            channel_id=channel_id,
            user_id=user_id,
            version=version,
        )
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return EffectiveConfigurationResponse(
        version=snapshot.version,
        values=tuple(
            EffectiveConfigValueResponse(
                key=key,
                value=value,
                source=EffectiveConfigSourceResponse(
                    scope_type=snapshot.sources[key].scope_type,
                    scope_id=snapshot.sources[key].scope_id,
                    version=snapshot.sources[key].version,
                ),
            )
            for key, value in snapshot.values.items()
        ),
    )


@router.post("/drafts", response_model=ConfigVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_draft(
    command: ConfigDraftCreate,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
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
        return _version_response(
            await service.create_draft(note=command.note, values=entries, actor_id=actor_id)
        )
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
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
) -> ConfigVersionResponse:
    """原子发布草稿，并将其设为唯一生效版本。"""
    try:
        return _version_response(await service.publish(version_id, actor_id=actor_id))
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConfigurationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConfigurationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.post("/versions/{version_id}/rollback", response_model=ConfigVersionResponse)
async def rollback_version(
    version_id: UUID,
    service: Annotated[ConfigurationService, Depends(get_configuration_service)],
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
) -> ConfigVersionResponse:
    """复制历史快照并发布为新的不可变版本。"""
    try:
        return _version_response(await service.rollback(version_id, actor_id=actor_id))
    except ConfigurationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ConfigurationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConfigurationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


def _secret_response(secret: SecretMetadata) -> SecretMetadataResponse:
    return SecretMetadataResponse(
        id=secret.id,
        key=secret.key,
        scope_type=secret.scope_type,
        scope_id=secret.scope_id,
        provider=secret.provider,
        masked_hint=secret.masked_hint,
        integrity_status=secret.integrity_status,
        created_at=secret.created_at,
        updated_at=secret.updated_at,
        last_tested_at=secret.last_tested_at,
    )


@router.get("/secrets", response_model=SecretListResponse)
async def list_secrets(
    service: Annotated[SecretManagementService, Depends(get_secret_management_service)],
) -> SecretListResponse:
    """仅列出密钥掩码和管理状态。"""
    return SecretListResponse(
        secrets=tuple(_secret_response(item) for item in await service.list_metadata())
    )


@router.post("/secrets", response_model=SecretMetadataResponse)
async def set_secret(
    command: SecretWriteCommand,
    service: Annotated[SecretManagementService, Depends(get_secret_management_service)],
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
) -> SecretMetadataResponse:
    """新增或覆盖一个作用域密钥，响应不包含明文。"""
    try:
        secret = await service.set_secret(
            key=command.key,
            scope_type=command.scope_type,
            scope_id=command.scope_id,
            plaintext=command.plaintext.get_secret_value(),
            actor_id=actor_id,
        )
    except ConfigurationValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    return _secret_response(secret)


@router.post("/secrets/{secret_id}/rotate", response_model=SecretMetadataResponse)
async def rotate_secret(
    secret_id: UUID,
    command: SecretRotateCommand,
    service: Annotated[SecretManagementService, Depends(get_secret_management_service)],
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
) -> SecretMetadataResponse:
    """以新材料轮换现有密钥引用。"""
    try:
        secret = await service.rotate_secret(
            secret_id, plaintext=command.plaintext.get_secret_value(), actor_id=actor_id
        )
    except SecretNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _secret_response(secret)


@router.post("/secrets/{secret_id}/test", response_model=SecretMetadataResponse)
async def test_secret_integrity(
    secret_id: UUID,
    service: Annotated[SecretManagementService, Depends(get_secret_management_service)],
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
) -> SecretMetadataResponse:
    """在服务端解密并验证完整性，不调用外部模型 API。"""
    try:
        return _secret_response(await service.test_secret(secret_id, actor_id=actor_id))
    except SecretNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except SecretOperationError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.delete("/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_secret(
    secret_id: UUID,
    service: Annotated[SecretManagementService, Depends(get_secret_management_service)],
    actor_id: Annotated[UUID, Depends(get_current_actor_id)],
) -> Response:
    """清除一个密钥引用及其加密材料。"""
    try:
        await service.clear_secret(secret_id, actor_id=actor_id)
    except SecretNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
