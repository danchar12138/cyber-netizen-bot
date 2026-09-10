"""无需外部基础设施的 API 契约冒烟测试。"""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr

from cnb_api.main import create_app
from cnb_application import AuthenticationError, permissions_for_role
from cnb_contracts import (
    AdminRoleListResponse,
    AdminSessionResponse,
    ApiErrorResponse,
    BootstrapSettingsResponse,
    CognitionResourceListResponse,
    CognitionResourceResponse,
    CognitiveRunTraceResponse,
    ComponentHealth,
    ConfigPackageDocument,
    ConfigRegistryResponse,
    ConfigVersionListResponse,
    ConfigVersionResponse,
    ConversationListResponse,
    ConversationResponse,
    DataLifecycleOverviewResponse,
    EvaluationReportResponse,
    EvaluationRunResponse,
    EvaluationSuiteResponse,
    HealthResponse,
    MessageAcceptedResponse,
    MessageFeedbackResponse,
    MessageListResponse,
    MessageSearchResponse,
    ObservabilityDashboardResponse,
    SystemOverviewResponse,
    TaskStatusResponse,
)
from cnb_domain import AdminPrincipal, AdminRole, DevelopmentIdentity, JsonValue
from cnb_infrastructure import (
    InMemoryMemoryRepository,
    MemoryAttachmentRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
    MemoryDataLifecycleRepository,
    MemoryObjectStorage,
    MemoryObservabilityRepository,
    Settings,
)


async def test_observability_dashboard_records_only_safe_request_metadata() -> None:
    repository = MemoryObservabilityRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        observability_repository=repository,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/v1/administration/session?secret=must-not-be-recorded")
        response = await client.get("/api/v1/observability/dashboard")

    payload = ObservabilityDashboardResponse.model_validate(response.json())
    assert response.status_code == 200
    assert payload.api.requests == 1
    assert payload.api.server_errors == 0
    assert payload.alerts == ()
    assert payload.models == ()


async def test_data_lifecycle_api_enforces_permissions_and_returns_safe_download_headers() -> None:
    identity = DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.user"),
        agent_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.agent"),
        user_name="本地开发者",
        agent_name="赛博网友",
    )
    lifecycle_repository = MemoryDataLifecycleRepository(identity)
    private_object_key = f"tenants/{identity.tenant_id}/attachments/private.txt"
    lifecycle_repository.seed_user_export(
        identity.user_id,
        data=cast(
            dict[str, JsonValue],
            {"profile": {"id": str(identity.user_id), "display_name": identity.user_name}},
        ),
        record_count=1,
        object_keys=(private_object_key,),
    )
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        data_lifecycle_repository=lifecycle_repository,
        object_storage=MemoryObjectStorage(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        overview_response = await client.get(
            "/api/v1/data-lifecycle/overview",
            headers={"X-CNB-Development-Role": "viewer"},
        )
        export_response = await client.post(
            "/api/v1/data-lifecycle/exports",
            json={"user_id": str(identity.user_id)},
            headers={"X-CNB-Development-Role": "operator"},
        )
        viewer_export = await client.post(
            "/api/v1/data-lifecycle/exports",
            json={"user_id": str(identity.user_id)},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        operator_forget = await client.post(
            "/api/v1/data-lifecycle/forget",
            json={
                "user_id": str(identity.user_id),
                "confirmation": f"确认永久遗忘 {identity.user_id}",
            },
            headers={"X-CNB-Development-Role": "operator"},
        )

    overview = DataLifecycleOverviewResponse.model_validate(overview_response.json())
    exported = export_response.json()
    assert overview_response.status_code == 200
    assert overview.policy.deleted_conversation_days == 30
    assert export_response.status_code == 200
    assert export_response.headers["content-disposition"].endswith('.json"')
    assert len(export_response.headers["x-content-sha256"]) == 64
    assert export_response.headers["x-export-run-id"]
    assert private_object_key not in export_response.text
    assert exported["schema_version"] == "cnb-user-export-v1"
    assert viewer_export.status_code == 403
    assert operator_forget.status_code == 403


class FakeOidcAuthenticator:
    """API 认证边界桩，不在集成测试中发起发现请求。"""

    def __init__(self, principal: AdminPrincipal) -> None:
        self._principal = principal

    async def authenticate(self, access_token: str) -> AdminPrincipal:
        if access_token != "signed-test-token":
            raise AuthenticationError("访问令牌无效或已过期")
        return self._principal


def _transport(
    repository: MemoryConfigurationRepository | None = None,
    *,
    deep_checks: bool = False,
    dependency_probe: object | None = None,
) -> ASGITransport:
    settings = Settings(environment="test", readiness_deep_checks=deep_checks)
    arguments: dict[str, object] = {
        "configuration_repository": repository or MemoryConfigurationRepository(),
        "conversation_repository": MemoryConversationRepository(),
    }
    if dependency_probe is not None:
        arguments["dependency_probe"] = dependency_probe
    return ASGITransport(app=create_app(settings, **arguments))  # pyright: ignore[reportArgumentType]


async def test_live_health() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert HealthResponse.model_validate(response.json()).status == "healthy"


async def test_admin_session_and_role_matrix_expose_server_permissions() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        session_response = await client.get(
            "/api/v1/administration/session",
            headers={"X-CNB-Development-Role": "viewer"},
        )
        roles_response = await client.get(
            "/api/v1/administration/roles",
            headers={"X-CNB-Development-Role": "viewer"},
        )

    session = AdminSessionResponse.model_validate(session_response.json())
    roles = AdminRoleListResponse.model_validate(roles_response.json())
    assert session.role == "viewer"
    assert "configuration:read" in session.permissions
    assert "configuration:write" not in session.permissions
    assert [item.role for item in roles.roles] == ["admin", "operator", "viewer"]


async def test_oidc_mode_requires_bearer_token_and_exposes_safe_browser_config() -> None:
    tenant_id, user_id, agent_id = uuid4(), uuid4(), uuid4()
    principal = AdminPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        display_name="OIDC 只读用户",
        role=AdminRole.VIEWER,
        permissions=permissions_for_role(AdminRole.VIEWER),
        authentication_mode="oidc",
    )
    settings = Settings(
        environment="test",
        authentication_mode="oidc",
        oidc_issuer_url="https://identity.example.test/realms/cnb",
        oidc_client_id="cyber-netizen-web",
        oidc_audience="cyber-netizen-api",
        oidc_tenant_id=tenant_id,
        oidc_agent_id=agent_id,
    )
    app = create_app(
        settings,
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        admin_authenticator=FakeOidcAuthenticator(principal),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        config = await client.get("/api/v1/auth/config")
        missing = await client.get("/api/v1/administration/session")
        invalid = await client.get(
            "/api/v1/administration/session",
            headers={"Authorization": "Basic invalid"},
        )
        authenticated = await client.get(
            "/api/v1/administration/session",
            headers={"Authorization": "Bearer signed-test-token"},
        )

    assert config.status_code == 200
    assert config.json() == {
        "mode": "oidc",
        "authority": "https://identity.example.test/realms/cnb",
        "client_id": "cyber-netizen-web",
        "scope": "openid profile email",
    }
    assert missing.status_code == invalid.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert authenticated.status_code == 200
    assert authenticated.json()["user_id"] == str(user_id)
    assert authenticated.json()["authentication_mode"] == "oidc"


def test_oidc_websocket_uses_bearer_subprotocol_without_echoing_token() -> None:
    tenant_id, user_id, agent_id = uuid4(), uuid4(), uuid4()
    principal = AdminPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        display_name="OIDC WebSocket 用户",
        role=AdminRole.ADMIN,
        permissions=permissions_for_role(AdminRole.ADMIN),
        authentication_mode="oidc",
    )
    app = create_app(
        Settings(
            environment="test",
            authentication_mode="oidc",
            oidc_issuer_url="https://identity.example.test/realms/cnb",
            oidc_client_id="cyber-netizen-web",
            oidc_tenant_id=tenant_id,
            oidc_agent_id=agent_id,
        ),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        admin_authenticator=FakeOidcAuthenticator(principal),
    )

    with TestClient(app) as client:
        response = cast(
            Response,
            client.post(  # pyright: ignore[reportUnknownMemberType]
                "/api/v1/chat/conversations",
                json={"title": "OIDC WebSocket 验证"},
                headers={"Authorization": "Bearer signed-test-token"},
            ),
        )
        conversation = ConversationResponse.model_validate(response.json())
        with client.websocket_connect(
            f"/api/v1/chat/conversations/{conversation.id}/events?after=0",
            subprotocols=["cnb.bearer", "signed-test-token"],
        ) as websocket:
            assert websocket.accepted_subprotocol == "cnb.bearer"
            event = websocket.receive_json()

    assert event["event_type"] == "conversation.created"


async def test_cognition_resource_publish_rollback_and_evaluation_api() -> None:
    persona_payload: dict[str, JsonValue] = {
        "identity": "自然、诚实的赛博网友",
        "purpose": "长期而尊重边界地交流",
        "principles": ["不捏造事实"],
        "boundaries": ["不泄露隐私"],
        "traits": {
            "warmth": 0.8,
            "curiosity": 0.7,
            "humor": 0.4,
            "directness": 0.6,
            "initiative": 0.5,
        },
        "style": {
            "address_style": "自然称呼对方",
            "sentence_length": "短句为主",
            "emoji_frequency": "少量",
            "preferred_phrases": [],
            "avoided_phrases": [],
        },
    }
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        tested = await client.post(
            "/api/v1/cognition/resources/test",
            json={"kind": "persona", "payload": persona_payload},
        )
        draft_response = await client.post(
            "/api/v1/cognition/resources",
            json={
                "kind": "persona",
                "key": "default",
                "name": "API 人格",
                "payload": persona_payload,
                "note": "集成测试",
            },
        )
        draft = CognitionResourceResponse.model_validate(draft_response.json())
        published_response = await client.post(f"/api/v1/cognition/resources/{draft.id}/publish")
        rollback_response = await client.post(f"/api/v1/cognition/resources/{draft.id}/rollback")
        listed_response = await client.get(
            "/api/v1/cognition/resources", params={"kind": "persona"}
        )
        evaluation_response = await client.post("/api/v1/cognition/evaluations/run")
        viewer_write = await client.post(
            "/api/v1/cognition/resources",
            json={
                "kind": "persona",
                "key": "denied",
                "name": "无权草稿",
                "payload": persona_payload,
            },
            headers={"X-CNB-Development-Role": "viewer"},
        )

    published = CognitionResourceResponse.model_validate(published_response.json())
    rolled_back = CognitionResourceResponse.model_validate(rollback_response.json())
    listed = CognitionResourceListResponse.model_validate(listed_response.json())
    evaluation = EvaluationSuiteResponse.model_validate(evaluation_response.json())
    assert tested.json()["valid"] is True
    assert draft_response.status_code == 201
    assert published.status == "published"
    assert rolled_back.status == "published"
    assert rolled_back.version == 2
    assert len(listed.items) == 2
    assert evaluation.passed == evaluation.total == 5
    assert viewer_write.status_code == 403


async def test_persisted_evaluation_replay_and_blind_review_api() -> None:
    """完整覆盖回放留痕、来源盲化、评分去盲和权限边界。"""
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        run_response = await client.post("/api/v1/evaluations/runs", json={})
        run = EvaluationRunResponse.model_validate(run_response.json())
        history = await client.get("/api/v1/evaluations/runs")
        detail = await client.get(f"/api/v1/evaluations/runs/{run.id}")
        assignment_response = await client.post(
            "/api/v1/evaluations/blind-assignments",
            json={"run_id": str(run.id)},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        assignment = assignment_response.json()
        review_response = await client.post(
            f"/api/v1/evaluations/blind-assignments/{assignment['id']}/reviews",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "preference": "a",
                "response_a_score": {
                    "persona_consistency": 4,
                    "naturalness": 4,
                    "empathy": 3,
                    "boundary_respect": 5,
                },
                "response_b_score": {
                    "persona_consistency": 3,
                    "naturalness": 3,
                    "empathy": 4,
                    "boundary_respect": 5,
                },
                "note": "API 盲评",
            },
        )
        duplicate = await client.post(
            f"/api/v1/evaluations/blind-assignments/{assignment['id']}/reviews",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "preference": "tie",
                "response_a_score": {
                    "persona_consistency": 3,
                    "naturalness": 3,
                    "empathy": 3,
                    "boundary_respect": 3,
                },
                "response_b_score": {
                    "persona_consistency": 3,
                    "naturalness": 3,
                    "empathy": 3,
                    "boundary_respect": 3,
                },
            },
        )
        report_response = await client.get("/api/v1/evaluations/report")
        viewer_suite_create = await client.post(
            "/api/v1/evaluations/suites",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "key": "denied",
                "name": "无权评测集",
                "cases": [
                    {
                        "case_key": "denied",
                        "category": "自然度",
                        "input_text": "你好",
                        "expected_action": "reply",
                        "reference_response": "你好呀。",
                    }
                ],
            },
        )

    report = EvaluationReportResponse.model_validate(report_response.json())
    assert run_response.status_code == 201
    assert run.passed == run.total == 5
    assert run.gate_passed is True
    assert run.input_tokens > 0
    assert history.json()["items"][0]["id"] == str(run.id)
    assert len(detail.json()["results"]) == 5
    assert assignment_response.status_code == 200
    assert set(assignment) == {
        "id",
        "case_key",
        "category",
        "input_text",
        "response_a",
        "response_b",
        "created_at",
    }
    assert review_response.status_code == 201
    assert review_response.json()["preference"] in {"candidate", "reference"}
    assert duplicate.status_code == 409
    assert report.total_runs == 1
    assert report.completed_reviews == 1
    assert report.pending_reviews == 3
    assert viewer_suite_create.status_code == 403


async def test_rbac_rejects_viewer_changes_and_operator_secret_access() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        viewer_change = await client.post(
            "/api/v1/configuration/drafts",
            json={"values": []},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        operator_change = await client.post(
            "/api/v1/configuration/drafts",
            json={"values": []},
            headers={"X-CNB-Development-Role": "operator"},
        )
        operator_secrets = await client.get(
            "/api/v1/configuration/secrets",
            headers={"X-CNB-Development-Role": "operator"},
        )

    assert viewer_change.status_code == 403
    assert ApiErrorResponse.model_validate(viewer_change.json()).error.code == "forbidden"
    assert operator_change.status_code == 201
    assert operator_secrets.status_code == 403


async def test_agent_user_management_bulk_confirmation_and_audit_flow() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        agents_response = await client.get("/api/v1/administration/agents")
        users_response = await client.get("/api/v1/administration/users")
        missing_confirmation = await client.post(
            "/api/v1/administration/agents/status",
            json={
                "ids": [identity["agent_id"]],
                "status": "disabled",
                "confirmed": False,
            },
        )
        update_response = await client.post(
            "/api/v1/administration/agents/status",
            json={
                "ids": [identity["agent_id"]],
                "status": "disabled",
                "confirmed": True,
            },
        )
        audit_response = await client.get(
            "/api/v1/administration/audit", params={"search": "agent"}
        )
        viewer_update = await client.post(
            "/api/v1/administration/users/status",
            json={
                "ids": [identity["user_id"]],
                "status": "disabled",
                "confirmed": True,
            },
            headers={"X-CNB-Development-Role": "viewer"},
        )

    assert agents_response.json()["items"][0]["id"] == identity["agent_id"]
    assert users_response.json()["items"][0]["id"] == identity["user_id"]
    assert missing_confirmation.status_code == 422
    assert update_response.json()["items"][0]["status"] == "disabled"
    assert audit_response.json()["items"][0]["action"] == "agent.status_updated"
    assert viewer_update.status_code == 403


async def test_multi_agent_creation_copy_selection_and_conversation_isolation() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        source_identity = (await client.get("/api/v1/chat/identity")).json()
        source_agent_id = source_identity["agent_id"]
        draft_response = await client.post(
            "/api/v1/cognition/resources",
            json={
                "kind": "prompt",
                "key": "chat.realizer",
                "name": "自然表达",
                "payload": {"template": "像熟悉的网友一样自然回应。"},
                "note": "复制测试基线",
            },
        )
        draft_id = draft_response.json()["id"]
        assert (
            await client.post(f"/api/v1/cognition/resources/{draft_id}/publish")
        ).status_code == 200

        blank_response = await client.post(
            "/api/v1/administration/agents",
            json={"name": "空白伙伴"},
        )
        copied_response = await client.post(
            f"/api/v1/administration/agents/{source_agent_id}/copy",
            json={"name": "人格副本"},
        )
        blank_agent_id = blank_response.json()["id"]
        copied_agent_id = copied_response.json()["id"]
        copied_resources = await client.get(
            "/api/v1/cognition/resources",
            headers={"X-CNB-Agent-ID": copied_agent_id},
        )

        source_conversation = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "源 Agent 会话"},
        )
        blank_conversation = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "空白 Agent 会话"},
            headers={"X-CNB-Agent-ID": blank_agent_id},
        )
        source_list = await client.get("/api/v1/chat/conversations")
        blank_list = await client.get(
            "/api/v1/chat/conversations",
            headers={"X-CNB-Agent-ID": blank_agent_id},
        )
        cross_agent_read = await client.get(
            f"/api/v1/chat/conversations/{source_conversation.json()['id']}/messages",
            headers={"X-CNB-Agent-ID": blank_agent_id},
        )
        unknown_agent = await client.get(
            "/api/v1/chat/identity",
            headers={"X-CNB-Agent-ID": str(uuid4())},
        )
        await client.post(
            "/api/v1/administration/agents/status",
            json={"ids": [blank_agent_id], "status": "disabled", "confirmed": True},
        )
        disabled_agent = await client.get(
            "/api/v1/chat/identity",
            headers={"X-CNB-Agent-ID": blank_agent_id},
        )

    assert blank_response.status_code == 201
    assert copied_response.status_code == 201
    copied = CognitionResourceListResponse.model_validate(copied_resources.json())
    assert len(copied.items) == 1
    assert copied.items[0].version == 1
    assert copied.items[0].agent_id == UUID(copied_agent_id)
    assert source_list.json()["items"][0]["title"] == "源 Agent 会话"
    assert blank_list.json()["items"][0]["id"] == blank_conversation.json()["id"]
    assert cross_agent_read.status_code == 404
    assert unknown_agent.status_code == 404
    assert disabled_agent.status_code == 409


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
    secret_definitions = [definition for definition in payload.definitions if definition.secret]
    assert secret_definitions
    assert all(definition.default is None for definition in secret_definitions)


async def test_system_overview_matches_registry_count() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        overview_response = await client.get("/api/v1/system/overview")
        registry_response = await client.get("/api/v1/configuration/definitions")

    overview = SystemOverviewResponse.model_validate(overview_response.json())
    registry = ConfigRegistryResponse.model_validate(registry_response.json())

    assert overview.configuration_definitions == len(registry.definitions)
    assert overview.environment == "test"


async def test_system_settings_and_task_status_hide_bootstrap_secrets() -> None:
    settings = Settings(
        environment="test",
        minio_endpoint_url="http://minio.internal:9000",
        minio_access_key=SecretStr("minio-test-user"),
        minio_secret_key=SecretStr("minio-test-secret"),
        minio_bucket="attachments",
        config_master_key=SecretStr("configured-test-key"),
        otel_exporter_otlp_endpoint="https://collector.internal/v1/traces",
        otel_exporter_otlp_headers=SecretStr("Authorization=Bearer%20telemetry-test-secret"),
    )
    app = create_app(
        settings,
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        settings_response = await client.get("/api/v1/system/settings")
        tasks_response = await client.get("/api/v1/system/tasks/status")

    bootstrap = BootstrapSettingsResponse.model_validate(settings_response.json())
    tasks = TaskStatusResponse.model_validate(tasks_response.json())
    assert bootstrap.object_storage_provider == "minio"
    assert bootstrap.minio_endpoint_url == "http://minio.internal:9000"
    assert bootstrap.minio_bucket == "attachments"
    assert bootstrap.minio_credentials_configured is True
    assert bootstrap.config_master_key_status == "configured"
    assert bootstrap.otel_enabled is False
    assert bootstrap.otel_exporter_configured is True
    assert "minio-test-user" not in settings_response.text
    assert "minio-test-secret" not in settings_response.text
    assert "configured-test-key" not in settings_response.text
    assert "collector.internal" not in settings_response.text
    assert "telemetry-test-secret" not in settings_response.text
    assert tasks.broker == "dramatiq-redis"
    assert tasks.worker.status == "not_checked"


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


async def test_configuration_package_export_and_safe_draft_import() -> None:
    repository = MemoryConfigurationRepository()
    async with AsyncClient(transport=_transport(repository), base_url="http://test") as client:
        draft_response = await client.post(
            "/api/v1/configuration/drafts",
            json={
                "note": "可移植基线",
                "values": [
                    {
                        "key": "cognition.context.max_tokens",
                        "scope_type": "system",
                        "value": 24000,
                    }
                ],
            },
        )
        draft = ConfigVersionResponse.model_validate(draft_response.json())
        await client.post(
            "/api/v1/configuration/secrets",
            json={
                "key": "model.openai.api_key",
                "scope_type": "system",
                "plaintext": "绝不能进入配置包的虚假密钥",
            },
        )

        export_response = await client.get(f"/api/v1/configuration/versions/{draft.id}/export")
        package = ConfigPackageDocument.model_validate(export_response.json())
        import_response = await client.post(
            "/api/v1/configuration/imports",
            json=package.model_dump(mode="json"),
        )
        incompatible_payload = package.model_dump(mode="json")
        incompatible_payload["schema_version"] = "99"
        incompatible_response = await client.post(
            "/api/v1/configuration/imports",
            json=incompatible_payload,
        )
        secret_value = "不允许混入普通配置包的材料"
        secret_payload = package.model_dump(mode="json")
        secret_payload["values"] = [
            {
                "key": "model.openai.api_key",
                "scope_type": "system",
                "scope_id": None,
                "value": secret_value,
            }
        ]
        secret_import_response = await client.post(
            "/api/v1/configuration/imports",
            json=secret_payload,
        )

    imported = ConfigVersionResponse.model_validate(import_response.json())
    assert export_response.status_code == 200
    assert export_response.headers["cache-control"] == "no-store"
    assert export_response.headers["content-disposition"] == (
        'attachment; filename="cnb-configuration-v1.json"'
    )
    assert package.format == "cnb-runtime-configuration"
    assert package.schema_version == "1"
    assert package.source.version == draft.version
    assert package.values[0].value == 24000
    assert "绝不能进入配置包的虚假密钥" not in export_response.text
    assert "plaintext" not in export_response.text
    assert import_response.status_code == 201
    assert imported.status == "draft"
    assert imported.version == 2
    assert imported.note == "从配置包 v1 导入：可移植基线"
    assert imported.values == draft.values
    assert incompatible_response.status_code == 422
    assert (
        "不支持的配置包 Schema 版本"
        in ApiErrorResponse.model_validate(incompatible_response.json()).error.message
    )
    assert secret_import_response.status_code == 422
    assert (
        "密钥配置必须通过密钥存储处理"
        in ApiErrorResponse.model_validate(secret_import_response.json()).error.message
    )
    assert secret_value not in secret_import_response.text


async def test_configuration_diff_effective_sources_and_secret_lifecycle() -> None:
    repository = MemoryConfigurationRepository()
    async with AsyncClient(transport=_transport(repository), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        draft_response = await client.post(
            "/api/v1/configuration/drafts",
            json={
                "note": "作用域与密钥测试",
                "values": [
                    {
                        "key": "model.chat.max_output_tokens",
                        "scope_type": "system",
                        "scope_id": None,
                        "value": 512,
                    },
                    {
                        "key": "model.chat.max_output_tokens",
                        "scope_type": "agent",
                        "scope_id": identity["agent_id"],
                        "value": 768,
                    },
                ],
            },
        )
        draft = ConfigVersionResponse.model_validate(draft_response.json())
        diff_response = await client.get(f"/api/v1/configuration/versions/{draft.id}/diff")
        await client.post(f"/api/v1/configuration/versions/{draft.id}/publish")
        effective_response = await client.get(
            "/api/v1/configuration/effective",
            params={
                "tenant_id": identity["tenant_id"],
                "agent_id": identity["agent_id"],
                "user_id": identity["user_id"],
            },
        )
        secret_response = await client.post(
            "/api/v1/configuration/secrets",
            json={
                "key": "model.openai.api_key",
                "scope_type": "agent",
                "scope_id": identity["agent_id"],
                "plaintext": "仅供契约测试的虚假凭证",
            },
        )
        secrets_response = await client.get("/api/v1/configuration/secrets")
        secret_id = secret_response.json()["id"]
        test_response = await client.post(f"/api/v1/configuration/secrets/{secret_id}/test")
        clear_response = await client.delete(f"/api/v1/configuration/secrets/{secret_id}")

    assert diff_response.status_code == 200
    assert len(diff_response.json()["changes"]) == 2
    assert effective_response.status_code == 200
    effective = {item["key"]: item for item in effective_response.json()["values"]}
    assert effective["model.chat.max_output_tokens"]["value"] == 768
    assert effective["model.chat.max_output_tokens"]["source"]["scope_type"] == "agent"
    assert secret_response.status_code == 200
    assert secret_response.json()["masked_hint"] == "••••虚假凭证"
    assert "plaintext" not in secret_response.text
    assert "仅供契约测试的虚假凭证" not in secret_response.text
    assert secrets_response.json()["secrets"][0]["configured"] is True
    assert test_response.json()["integrity_status"] == "valid"
    assert clear_response.status_code == 204


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
    error = ApiErrorResponse.model_validate(response.json())
    assert "不能大于" in error.error.message
    assert response.headers["X-Request-ID"] == error.error.request_id


async def test_configuration_missing_version_returns_404() -> None:
    missing_id = uuid4()
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.get(f"/api/v1/configuration/versions/{missing_id}")

    assert response.status_code == 404
    assert "配置版本不存在" in ApiErrorResponse.model_validate(response.json()).error.message


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
    assert "草稿不能" in ApiErrorResponse.model_validate(rollback_response.json()).error.message
    assert publish_response.status_code == 200
    assert republish_response.status_code == 409
    assert "草稿状态" in ApiErrorResponse.model_validate(republish_response.json()).error.message


async def test_internal_chat_persists_and_completes_a_streamed_turn() -> None:
    repository = MemoryConversationRepository()
    settings = Settings(environment="test")
    transport = ASGITransport(
        app=create_app(
            settings,
            configuration_repository=MemoryConfigurationRepository(),
            conversation_repository=repository,
        )
    )
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        identity_response = await client.get("/api/v1/chat/identity")
        assert identity_response.status_code == 200

        create_response = await client.post(
            "/api/v1/chat/conversations", json={"title": "API 流式测试"}
        )
        assert create_response.status_code == 201
        conversation = ConversationResponse.model_validate(create_response.json())

        client_message_id = str(uuid4())
        accepted_response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"client_message_id": client_message_id, "content": "你好"},
        )
        assert accepted_response.status_code == 202
        accepted = MessageAcceptedResponse.model_validate(accepted_response.json())
        assert accepted.idempotent_replay is False

        messages: MessageListResponse | None = None
        for _ in range(50):
            messages_response = await client.get(
                f"/api/v1/chat/conversations/{conversation.id}/messages"
            )
            messages = MessageListResponse.model_validate(messages_response.json())
            if messages.items[-1].status == "completed":
                break
            await asyncio.sleep(0.01)

        replay_response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"client_message_id": client_message_id, "content": "不会重复"},
        )
        conversations_response = await client.get("/api/v1/chat/conversations")
        trace_response = await client.get(f"/api/v1/cognition/runs/{accepted.run.id}/trace")

    replay = MessageAcceptedResponse.model_validate(replay_response.json())
    conversations = ConversationListResponse.model_validate(conversations_response.json())
    trace = CognitiveRunTraceResponse.model_validate(trace_response.json())
    assert messages is not None
    assert replay.idempotent_replay is True
    assert replay.run.id == accepted.run.id
    assert len(messages.items) == 2
    assert messages.items[-1].status == "completed"
    assert "你好" in messages.items[-1].content
    assert conversations.items[0].id == conversation.id
    assert [item.stage for item in trace.steps] == [
        "perception",
        "context_assembly",
        "memory_recall",
        "social_mind",
        "deliberation",
        "policy_gate",
        "realizer",
    ]
    assert trace.candidates[0].selected is True
    assert trace.model_invocations[0].status == "completed"


async def test_internal_chat_suppresses_response_when_user_requests_silence() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        conversation_response = await client.post(
            "/api/v1/chat/conversations", json={"title": "不回复边界"}
        )
        conversation = ConversationResponse.model_validate(conversation_response.json())
        accepted_response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"client_message_id": str(uuid4()), "content": "我想静静，不用回复"},
        )
        accepted = MessageAcceptedResponse.model_validate(accepted_response.json())
        messages: MessageListResponse | None = None
        for _ in range(50):
            response = await client.get(f"/api/v1/chat/conversations/{conversation.id}/messages")
            messages = MessageListResponse.model_validate(response.json())
            if messages.items[-1].status == "suppressed":
                break
            await asyncio.sleep(0.01)
        trace_response = await client.get(f"/api/v1/cognition/runs/{accepted.run.id}/trace")

    trace = CognitiveRunTraceResponse.model_validate(trace_response.json())
    assert messages is not None
    assert messages.items[-1].status == "suppressed"
    assert messages.items[-1].content == ""
    assert trace.candidates[0].action == "no_reply"
    assert trace.model_invocations == ()


async def test_internal_chat_rejects_invalid_cursor_and_unknown_conversation() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        invalid_cursor = await client.get(
            "/api/v1/chat/conversations", params={"cursor": "不是游标"}
        )
        missing_conversation = await client.get(f"/api/v1/chat/conversations/{uuid4()}/messages")

    assert invalid_cursor.status_code == 422
    assert ApiErrorResponse.model_validate(invalid_cursor.json()).error.message == "分页游标无效"
    assert missing_conversation.status_code == 404


async def test_internal_chat_management_branch_feedback_and_search_flow() -> None:
    repository = MemoryConversationRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=repository,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created_response = await client.post(
            "/api/v1/chat/conversations", json={"title": "会话管理测试"}
        )
        conversation = ConversationResponse.model_validate(created_response.json())
        accepted_response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={"client_message_id": str(uuid4()), "content": "你好，搜索我"},
        )
        accepted = MessageAcceptedResponse.model_validate(accepted_response.json())
        for _ in range(50):
            message_response = await client.get(
                f"/api/v1/chat/conversations/{conversation.id}/messages"
            )
            messages = MessageListResponse.model_validate(message_response.json())
            if messages.items[-1].status == "completed":
                break
            await asyncio.sleep(0.01)

        update_response = await client.patch(
            f"/api/v1/chat/conversations/{conversation.id}",
            json={"title": "已置顶会话", "status": "archived", "pinned": True},
        )
        feedback_response = await client.put(
            f"/api/v1/chat/messages/{accepted.response_message.id}/feedback",
            json={"rating": "positive", "comment": "自然"},
        )
        feedback = MessageFeedbackResponse.model_validate(feedback_response.json())
        feedback_list = await client.get(f"/api/v1/chat/conversations/{conversation.id}/feedback")
        search_response = await client.get(
            "/api/v1/chat/messages/search", params={"query": "搜索我"}
        )
        search = MessageSearchResponse.model_validate(search_response.json())
        regenerate_response = await client.post(
            f"/api/v1/chat/messages/{accepted.response_message.id}/regenerate",
            json={"client_request_id": str(uuid4())},
        )
        branch_response = await client.post(
            f"/api/v1/chat/messages/{accepted.user_message.id}/edit",
            json={"client_message_id": str(uuid4()), "content": "你好"},
        )
        branch = MessageAcceptedResponse.model_validate(branch_response.json())
        delete_feedback_response = await client.delete(
            f"/api/v1/chat/messages/{accepted.response_message.id}/feedback"
        )
        delete_conversation_response = await client.delete(
            f"/api/v1/chat/conversations/{conversation.id}"
        )
        conversations_response = await client.get("/api/v1/chat/conversations")

    updated = ConversationResponse.model_validate(update_response.json())
    regenerated = MessageAcceptedResponse.model_validate(regenerate_response.json())
    assert updated.title == "已置顶会话"
    assert updated.status == "archived"
    assert updated.pinned_at is not None
    assert feedback.rating == "positive"
    assert feedback_list.json()["items"][0]["id"] == str(feedback.id)
    assert accepted.user_message.id in {item.message.id for item in search.items}
    assert regenerated.response_message.id != accepted.response_message.id
    assert branch.user_message.edited_from_id == accepted.user_message.id
    assert branch.user_message.conversation_id != conversation.id
    assert delete_feedback_response.status_code == 204
    assert delete_conversation_response.status_code == 200
    remaining = ConversationListResponse.model_validate(conversations_response.json()).items
    assert [item.id for item in remaining] == [branch.user_message.conversation_id]


async def test_attachment_api_rejects_unsafe_types_and_unknown_resources() -> None:
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        missing_conversation = await client.post(
            "/api/v1/chat/attachments/reservations",
            json={
                "conversation_id": str(uuid4()),
                "client_message_id": str(uuid4()),
                "original_name": "说明.txt",
                "content_type": "text/plain",
                "size_bytes": 10,
                "sha256": "0" * 64,
            },
        )
        conversation_response = await client.post(
            "/api/v1/chat/conversations", json={"title": "附件 API"}
        )
        conversation = ConversationResponse.model_validate(conversation_response.json())
        unsafe = await client.post(
            "/api/v1/chat/attachments/reservations",
            json={
                "conversation_id": str(conversation.id),
                "client_message_id": str(uuid4()),
                "original_name": "危险.exe",
                "content_type": "application/x-msdownload",
                "size_bytes": 10,
                "sha256": "0" * 64,
            },
        )
        missing_attachment = await client.post(f"/api/v1/chat/attachments/{uuid4()}/complete")
        assert identity["tenant_id"]

    assert missing_conversation.status_code == 404
    assert unsafe.status_code == 409
    assert missing_attachment.status_code == 404


async def test_attachment_api_upload_complete_send_and_preview_flow() -> None:
    attachment_repository = MemoryAttachmentRepository()
    object_storage = MemoryObjectStorage()
    conversation_repository = MemoryConversationRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=conversation_repository,
        attachment_repository=attachment_repository,
        object_storage=object_storage,
    )
    content = b"api attachment content"
    digest = sha256(content).hexdigest()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        conversation_response = await client.post(
            "/api/v1/chat/conversations", json={"title": "附件上传闭环"}
        )
        conversation = ConversationResponse.model_validate(conversation_response.json())
        client_message_id = uuid4()
        reservation_response = await client.post(
            "/api/v1/chat/attachments/reservations",
            json={
                "conversation_id": str(conversation.id),
                "client_message_id": str(client_message_id),
                "original_name": "资料.txt",
                "content_type": "text/plain",
                "size_bytes": len(content),
                "sha256": digest,
            },
        )
        reservation = reservation_response.json()
        attachment_id = UUID(reservation["attachment"]["id"])
        persisted = await attachment_repository.get_attachment_for_user(
            attachment_id, UUID(identity["user_id"])
        )
        assert persisted is not None
        object_storage.put_for_test(
            object_key=persisted.object_key,
            content=content,
            content_type="text/plain",
        )
        complete_response = await client.post(f"/api/v1/chat/attachments/{attachment_id}/complete")
        send_response = await client.post(
            f"/api/v1/chat/conversations/{conversation.id}/messages",
            json={
                "client_message_id": str(client_message_id),
                "content": "请查看附件",
                "attachment_ids": [str(attachment_id)],
            },
        )
        listed_response = await client.get(
            f"/api/v1/chat/conversations/{conversation.id}/attachments"
        )
        preview_response = await client.get(f"/api/v1/chat/attachments/{attachment_id}/preview")
        messages: MessageListResponse | None = None
        for _ in range(50):
            messages_response = await client.get(
                f"/api/v1/chat/conversations/{conversation.id}/messages"
            )
            messages = MessageListResponse.model_validate(messages_response.json())
            if messages.items[-1].status == "completed":
                break
            await asyncio.sleep(0.01)

    accepted = MessageAcceptedResponse.model_validate(send_response.json())
    attached = await attachment_repository.get_attachment_for_user(
        attachment_id, UUID(identity["user_id"])
    )
    assert reservation_response.status_code == 201
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "ready"
    assert send_response.status_code == 202
    assert accepted.user_message.content == "请查看附件"
    assert [part.kind.value for part in accepted.user_message.parts] == ["markdown", "file"]
    assert accepted.user_message.parts[1].attachment_id == attachment_id
    assert accepted.user_message.parts[1].file_name == "资料.txt"
    assert attached is not None
    assert attached.status.value == "attached"
    assert attached.message_id == accepted.user_message.id
    assert listed_response.status_code == 200
    assert listed_response.json()["items"][0]["id"] == str(attachment_id)
    assert preview_response.status_code == 200
    assert preview_response.json()["url"].startswith("memory://download/")
    assert messages is not None
    assert messages.items[-1].status == "completed"
    assert "api attachment content" in messages.items[-1].content


async def test_api_validation_errors_use_versioned_envelope_and_request_id() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "x" * 201},
            headers={"X-Request-ID": "test-request-id"},
        )

    payload = ApiErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert payload.schema_version == "1"
    assert payload.error.code == "validation_error"
    assert payload.error.request_id == "test-request-id"
    assert payload.error.details[0].field == "body.title"


async def test_memory_relationship_episode_and_index_management_api() -> None:
    memory_repository = InMemoryMemoryRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        memory_repository=memory_repository,
    )
    now = datetime.now(UTC).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        user_id = identity["user_id"]
        viewer_write = await client.post(
            "/api/v1/memory/memories",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "kind": "semantic",
                "content": "只读角色不能创建",
                "event_at": now,
                "sources": [
                    {
                        "kind": "import",
                        "source_id": "viewer-attempt",
                        "occurred_at": now,
                    }
                ],
            },
        )
        assert viewer_write.status_code == 403

        first_response = await client.post(
            "/api/v1/memory/memories",
            json={
                "user_id": user_id,
                "kind": "semantic",
                "visibility": "user",
                "content": "用户的猫叫月饼",
                "event_at": now,
                "confidence": 0.7,
                "importance": 0.9,
                "emotional_weight": 0.2,
                "sensitivity": "normal",
                "confirmation": "unconfirmed",
                "sources": [
                    {
                        "kind": "user_statement",
                        "source_id": "message:cat-name",
                        "excerpt": "我的猫叫月饼",
                        "is_verbatim": True,
                        "occurred_at": now,
                    }
                ],
            },
        )
        second_response = await client.post(
            "/api/v1/memory/memories",
            json={
                "user_id": user_id,
                "kind": "episodic",
                "content": "一起聊过猫咪的饮食",
                "event_at": now,
                "sources": [
                    {
                        "kind": "message",
                        "source_id": "message:cat-food",
                        "excerpt": "猫最近不爱吃饭",
                        "is_verbatim": True,
                        "occurred_at": now,
                    }
                ],
            },
        )
        assert first_response.status_code == 201
        assert second_response.status_code == 201
        first = first_response.json()["memory"]
        second = second_response.json()["memory"]

        listed = await client.get(f"/api/v1/memory/memories?user_id={user_id}&status=active")
        viewer_listed = await client.get(
            "/api/v1/memory/memories",
            headers={"X-CNB-Development-Role": "viewer"},
        )
        recalled = await client.post(
            "/api/v1/memory/recall",
            json={"user_id": user_id, "query": "我的猫叫什么", "limit": 5},
        )
        assert listed.status_code == viewer_listed.status_code == 200
        assert len(listed.json()["items"]) == 2
        assert recalled.status_code == 200
        assert recalled.json()["items"][0]["memory"]["id"] == first["id"]

        confirmed = await client.post(
            f"/api/v1/memory/memories/{first['id']}/confirmation",
            json={"confirmation": "confirmed"},
        )
        corrected = await client.post(
            f"/api/v1/memory/memories/{first['id']}/corrections",
            json={
                "content": "用户的猫叫月饼，生日在春天",
                "event_at": now,
                "note": "用户补充",
            },
        )
        corrected_memory = corrected.json()["memory"]
        conflict = await client.post(
            f"/api/v1/memory/memories/{corrected_memory['id']}/conflicts",
            json={"target_memory_id": second["id"], "note": "时间描述有冲突"},
        )
        assert confirmed.json()["confirmation"] == "confirmed"
        assert corrected.status_code == 201
        assert corrected_memory["version"] == 2
        assert conflict.status_code == 201

        relationship = await client.post(
            "/api/v1/memory/relationship/events",
            json={
                "user_id": user_id,
                "event_type": "memory_confirmed",
                "affinity_delta": 0.8,
                "trust_delta": 0.8,
                "familiarity_delta": 0.8,
                "summary": "已形成稳定信任。",
                "boundaries": ["不主动追问敏感信息"],
                "evidence_memory_id": corrected_memory["id"],
            },
        )
        relationship_read = await client.get(f"/api/v1/memory/relationship?user_id={user_id}")
        assert relationship.status_code == 201
        assert relationship_read.json()["relationship"]["stage"] == "trusted"

        conversation = await client.post(
            "/api/v1/chat/conversations", json={"title": "跨会话记忆验证"}
        )
        accepted = await client.post(
            f"/api/v1/chat/conversations/{conversation.json()['id']}/messages",
            json={"client_message_id": str(uuid4()), "content": "你还记得我的猫吗？"},
        )
        trace_payload: dict[str, object] = {}
        for _ in range(50):
            trace_response = await client.get(
                f"/api/v1/cognition/runs/{accepted.json()['run']['id']}/trace"
            )
            trace_payload = trace_response.json()
            if trace_payload.get("steps"):
                break
            await asyncio.sleep(0.01)
        memory_step = next(
            item
            for item in cast(list[dict[str, object]], trace_payload["steps"])
            if item["stage"] == "memory_recall"
        )
        memory_detail = cast(dict[str, object], memory_step["detail"])
        assert corrected_memory["id"] in cast(list[str], memory_detail["memory_ids"])
        assert memory_detail["relationship_version"] == 1

        conversation_id = str(uuid4())
        message_id = str(uuid4())
        episode = await client.post(
            "/api/v1/memory/episodes",
            json={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "title": "关于月饼的对话",
                "summary": "用户介绍了猫咪月饼。",
                "started_at": now,
                "ended_at": now,
                "source_message_ids": [message_id],
            },
        )
        closed_episode = await client.post(
            f"/api/v1/memory/episodes/{episode.json()['id']}/close",
            json={"consolidate": True},
        )
        assert episode.status_code == 201
        assert closed_episode.json()["status"] == "consolidated"

        forgotten = await client.post(
            f"/api/v1/memory/memories/{second['id']}/forget",
            json={"confirmed": True},
        )
        forgotten_detail = await client.get(f"/api/v1/memory/memories/{second['id']}")
        assert forgotten.json()["content"] is None
        assert forgotten_detail.json()["sources"][0]["excerpt"] is None
        assert second["id"] not in memory_repository.embeddings

        rebuild = await client.post(
            "/api/v1/memory/index-jobs",
            json={"user_id": user_id, "confirmed": True},
        )
        jobs = await client.get("/api/v1/memory/index-jobs")
        background_jobs = await client.get(
            "/api/v1/tasks/jobs", params={"kind": "embedding_rebuild"}
        )
        assert rebuild.status_code == 201
        assert rebuild.json()["status"] == "pending"
        assert jobs.json()["items"][0]["id"] == rebuild.json()["id"]
        assert background_jobs.json()["items"][0]["kind"] == "embedding_rebuild"
        assert background_jobs.json()["items"][0]["payload_keys"] == [
            "actor_id",
            "agent_id",
            "index_job_id",
        ]


async def test_task_dashboard_and_scheduled_action_management_api() -> None:
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        scheduled_for = datetime.now(UTC)
        create_response = await client.post(
            "/api/v1/tasks/scheduled-actions",
            json={
                "user_id": identity["user_id"],
                "kind": "proactive_message",
                "scheduled_for": scheduled_for.isoformat(),
                "expires_at": (
                    scheduled_for.replace(microsecond=0) + timedelta(days=1)
                ).isoformat(),
                "idempotency_key": "api:proactive:first",
                "reason": "跟进用户明确要求稍后提醒的事项",
                "payload": {"importance": 0.8, "confidence": 0.9},
                "social_cost": 1,
            },
        )
        duplicate = await client.post(
            "/api/v1/tasks/scheduled-actions",
            json={
                "user_id": identity["user_id"],
                "kind": "proactive_message",
                "scheduled_for": scheduled_for.isoformat(),
                "expires_at": (
                    scheduled_for.replace(microsecond=0) + timedelta(days=1)
                ).isoformat(),
                "idempotency_key": "api:proactive:first",
                "reason": "重复请求不应创建第二个行为",
                "payload": {},
                "social_cost": 1,
            },
        )
        viewer_create = await client.post(
            "/api/v1/tasks/scheduled-actions",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "user_id": identity["user_id"],
                "kind": "proactive_message",
                "scheduled_for": scheduled_for.isoformat(),
                "idempotency_key": "api:proactive:viewer",
                "reason": "无权创建",
            },
        )
        dashboard = await client.get("/api/v1/tasks/dashboard")
        listed = await client.get("/api/v1/tasks/scheduled-actions")
        action_id = create_response.json()["id"]
        canceled = await client.post(
            f"/api/v1/tasks/scheduled-actions/{action_id}/cancel",
            json={"confirmed": True},
        )
        task_status = await client.get("/api/v1/system/tasks/status")

    assert create_response.status_code == duplicate.status_code == 201
    assert duplicate.json()["id"] == action_id
    assert viewer_create.status_code == 403
    assert dashboard.json()["scheduled"] == 1
    assert len(listed.json()["items"]) == 1
    assert canceled.json()["status"] == "canceled"
    assert task_status.json()["worker"]["status"] == "not_checked"


async def test_channel_management_simulation_delivery_and_secret_boundary_api() -> None:
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = await client.get("/api/v1/channels/catalog")
        model_capabilities = await client.get("/api/v1/channels/model-capabilities")
        web = await client.post(
            "/api/v1/channels",
            json={
                "name": "内部 Web",
                "platform": "web",
                "status": "enabled",
                "rate_limit_per_minute": 10,
                "settings": {"audience": "internal"},
            },
        )
        web_id = web.json()["id"]
        delivery_command = {
            "recipient_id": "browser-session",
            "blocks": [{"kind": "markdown", "text": "你好，**渠道**"}],
            "idempotency_key": "api:web:delivery:1",
            "request_streaming": True,
            "proactive": False,
        }
        first_delivery = await client.post(
            f"/api/v1/channels/{web_id}/deliveries",
            json=delivery_command,
        )
        replayed_delivery = await client.post(
            f"/api/v1/channels/{web_id}/deliveries",
            json=delivery_command,
        )
        inbound = await client.post(
            f"/api/v1/channels/{web_id}/simulate-inbound",
            json={
                "payload": {
                    "external_event_id": "web:event:1",
                    "sender_external_id": "local-user",
                    "conversation_external_id": "local-conversation",
                    "text": "模拟入站消息",
                    "occurred_at": datetime.now(UTC).isoformat(),
                }
            },
        )
        simulation = await client.post(
            "/api/v1/channels/simulate",
            json={
                "platform": "discord",
                "blocks": [{"kind": "markdown", "text": "a" * 2500}],
                "request_streaming": True,
                "thread_id": "thread-1",
                "edit_message_id": "message-1",
                "proactive": False,
            },
        )
        feishu = await client.post(
            "/api/v1/channels",
            json={
                "name": "飞书占位",
                "platform": "feishu",
                "status": "enabled",
                "rate_limit_per_minute": 60,
                "settings": {"app_id_hint": "cli_test"},
                "credential": "不能通过响应返回的渠道密钥",
            },
        )
        feishu_id = feishu.json()["id"]
        tested = await client.post(f"/api/v1/channels/{feishu_id}/connection-test")
        placeholder_delivery = await client.post(
            f"/api/v1/channels/{feishu_id}/deliveries",
            json={
                **delivery_command,
                "idempotency_key": "api:feishu:delivery:1",
            },
        )
        events = await client.get("/api/v1/channels/diagnostics/events", params={"limit": 100})
        viewer_create = await client.post(
            "/api/v1/channels",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "name": "无权创建",
                "platform": "web",
                "status": "disabled",
                "rate_limit_per_minute": 10,
            },
        )

    assert catalog.status_code == model_capabilities.status_code == 200
    assert [item["platform"] for item in catalog.json()["items"]] == [
        "web",
        "feishu",
        "discord",
        "telegram",
    ]
    assert model_capabilities.json()["items"][1]["document_input"] is True
    assert web.status_code == 201
    assert first_delivery.status_code == replayed_delivery.status_code == 200
    assert replayed_delivery.json()["idempotent_replay"] is True
    assert inbound.json()["blocks"][0]["text"] == "模拟入站消息"
    assert simulation.status_code == 200
    assert "streaming_to_buffered" in simulation.json()["degradations"]
    assert "long_text_split" in simulation.json()["degradations"]
    assert feishu.status_code == 201
    assert feishu.json()["credential_configured"] is True
    assert "不能通过响应返回" not in feishu.text
    assert tested.json()["health_status"] == "not_configured"
    assert placeholder_delivery.status_code == 503
    assert events.status_code == 200
    assert all("text" not in item["payload_summary"] for item in events.json()["items"])
    assert viewer_create.status_code == 403


def test_internal_chat_websocket_replays_from_sequence_and_responds_to_ping() -> None:
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    with TestClient(app) as client:
        response = cast(
            Response,
            client.post(  # pyright: ignore[reportUnknownMemberType]
                "/api/v1/chat/conversations", json={"title": "重连测试"}
            ),
        )
        conversation = ConversationResponse.model_validate(response.json())

        with client.websocket_connect(
            f"/api/v1/chat/conversations/{conversation.id}/events?after=0"
        ) as websocket:
            first_event = websocket.receive_json()
            assert first_event["sequence"] == 1
            assert first_event["event_type"] == "conversation.created"
            websocket.send_text("ping")
            heartbeat = websocket.receive_json()
            assert heartbeat["event_type"] == "system.heartbeat"
            assert heartbeat["last_sequence"] == 1

        with client.websocket_connect(
            f"/api/v1/chat/conversations/{conversation.id}/events?after=1"
        ) as resumed:
            resumed.send_text("ping")
            heartbeat = resumed.receive_json()
            assert heartbeat["event_type"] == "system.heartbeat"
            assert heartbeat["last_sequence"] == 1
