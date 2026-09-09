"""无需外部基础设施的 API 契约冒烟测试。"""

from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from cnb_api.main import create_app
from cnb_contracts import (
    ComponentHealth,
    ConfigRegistryResponse,
    ConfigVersionListResponse,
    ConfigVersionResponse,
    HealthResponse,
    SystemOverviewResponse,
)
from cnb_infrastructure import MemoryConfigurationRepository, Settings


def _transport(
    repository: MemoryConfigurationRepository | None = None,
    *,
    deep_checks: bool = False,
    dependency_probe: object | None = None,
) -> ASGITransport:
    settings = Settings(environment="test", readiness_deep_checks=deep_checks)
    arguments: dict[str, object] = {
        "configuration_repository": repository or MemoryConfigurationRepository()
    }
    if dependency_probe is not None:
        arguments["dependency_probe"] = dependency_probe
    return ASGITransport(app=create_app(settings, **arguments))  # pyright: ignore[reportArgumentType]


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


async def test_ready_health_returns_503_when_a_dependency_is_degraded() -> None:
    async def degraded_probe(_: Settings) -> tuple[ComponentHealth, ...]:
        return (
            ComponentHealth(name="postgresql", status="healthy"),
            ComponentHealth(name="redis", status="degraded", detail="ConnectionError"),
        )

    async with AsyncClient(
        transport=_transport(deep_checks=True, dependency_probe=degraded_probe),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    health = HealthResponse.model_validate(response.json())
    assert health.status == "degraded"


async def test_ready_health_returns_200_when_all_dependencies_are_healthy() -> None:
    async def healthy_probe(_: Settings) -> tuple[ComponentHealth, ...]:
        return (
            ComponentHealth(name="postgresql", status="healthy"),
            ComponentHealth(name="redis", status="healthy"),
            ComponentHealth(name="object_storage", status="healthy"),
        )

    async with AsyncClient(
        transport=_transport(deep_checks=True, dependency_probe=healthy_probe),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    health = HealthResponse.model_validate(response.json())
    assert health.status == "ready"
    assert all(item.status == "healthy" for item in health.components)


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


async def test_configuration_draft_publish_and_rollback_flow() -> None:
    repository = MemoryConfigurationRepository()
    async with AsyncClient(transport=_transport(repository), base_url="http://test") as client:
        draft_response = await client.post(
            "/api/v1/configuration/drafts",
            json={
                "note": "首个配置版本",
                "values": [
                    {
                        "key": "cognition.context.max_tokens",
                        "scope_type": "system",
                        "value": 32000,
                    }
                ],
            },
        )
        assert draft_response.status_code == 201
        draft = ConfigVersionResponse.model_validate(draft_response.json())
        assert draft.status == "draft"

        publish_response = await client.post(f"/api/v1/configuration/versions/{draft.id}/publish")
        assert publish_response.status_code == 200
        published = ConfigVersionResponse.model_validate(publish_response.json())
        assert published.status == "published"

        rollback_response = await client.post(
            f"/api/v1/configuration/versions/{published.id}/rollback"
        )
        assert rollback_response.status_code == 200
        rollback = ConfigVersionResponse.model_validate(rollback_response.json())
        assert rollback.status == "published"
        assert rollback.version == 2
        assert rollback.values == published.values

        versions_response = await client.get("/api/v1/configuration/versions")

    versions = ConfigVersionListResponse.model_validate(versions_response.json())
    assert [item.version for item in versions.versions] == [2, 1]
    assert versions.versions[1].status == "superseded"


async def test_configuration_validation_error_is_friendly() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/configuration/drafts",
            json={
                "values": [
                    {
                        "key": "memory.recall.limit",
                        "scope_type": "system",
                        "value": 1000,
                    }
                ]
            },
        )

    assert response.status_code == 422
    assert "不能大于" in response.json()["detail"]


async def test_configuration_missing_version_returns_404() -> None:
    missing_id = uuid4()
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get(f"/api/v1/configuration/versions/{missing_id}")

    assert response.status_code == 404
    assert "配置版本不存在" in response.json()["detail"]


async def test_configuration_rejects_republishing_and_rolling_back_a_draft() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        draft_response = await client.post(
            "/api/v1/configuration/drafts",
            json={"note": "冲突测试", "values": []},
        )
        draft = ConfigVersionResponse.model_validate(draft_response.json())

        rollback_response = await client.post(f"/api/v1/configuration/versions/{draft.id}/rollback")
        publish_response = await client.post(f"/api/v1/configuration/versions/{draft.id}/publish")
        republish_response = await client.post(f"/api/v1/configuration/versions/{draft.id}/publish")

    assert rollback_response.status_code == 409
    assert "草稿不能" in rollback_response.json()["detail"]
    assert publish_response.status_code == 200
    assert republish_response.status_code == 409
    assert "草稿状态" in republish_response.json()["detail"]
