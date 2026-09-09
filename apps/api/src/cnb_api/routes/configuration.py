"""Schema-driven configuration management routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from cnb_api.dependencies import get_configuration_registry
from cnb_application import ConfigurationRegistry
from cnb_contracts import ConfigDefinitionResponse, ConfigRegistryResponse

router = APIRouter(prefix="/configuration", tags=["configuration"])


@router.get("/definitions", response_model=ConfigRegistryResponse)
async def list_definitions(
    registry: Annotated[ConfigurationRegistry, Depends(get_configuration_registry)],
) -> ConfigRegistryResponse:
    """Expose safe schemas and defaults; secret material is never included."""
    return ConfigRegistryResponse(
        definitions=tuple(
            ConfigDefinitionResponse.model_validate(definition) for definition in registry.all()
        )
    )
