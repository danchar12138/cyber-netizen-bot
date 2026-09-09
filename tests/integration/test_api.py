"""API contract smoke tests without external infrastructure."""

from httpx import ASGITransport, AsyncClient

from cnb_api.main import create_app
from cnb_contracts import ConfigRegistryResponse, HealthResponse, SystemOverviewResponse
from cnb_infrastructure import Settings


def _transport() -> ASGITransport:
    return ASGITransport(app=create_app(Settings(environment="test")))


async def test_live_health() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert HealthResponse.model_validate(response.json()).status == "healthy"


async def test_ready_health_discloses_disabled_deep_checks() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get("/health/ready")

    health = HealthResponse.model_validate(response.json())
    assert health.status == "ready"
    assert health.components[0].status == "not_checked"


async def test_configuration_definitions_never_contain_secret_values() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get("/api/v1/configuration/definitions")

    assert response.status_code == 200
    payload = ConfigRegistryResponse.model_validate(response.json())
    assert payload.schema_version == "1"
    assert payload.definitions
    assert all(not definition.secret for definition in payload.definitions)


async def test_system_overview_matches_registry_count() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        overview_response = await client.get("/api/v1/system/overview")
        registry_response = await client.get("/api/v1/configuration/definitions")

    overview = SystemOverviewResponse.model_validate(overview_response.json())
    registry = ConfigRegistryResponse.model_validate(registry_response.json())

    assert overview.configuration_definitions == len(registry.definitions)
    assert overview.environment == "test"
