"""无需外部基础设施的 API 契约冒烟测试。"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr

from cnb_adapters import ChannelAdapterRegistry, TelegramChannelAdapter
from cnb_api.main import create_app
from cnb_application import AuthenticationError, BackgroundTaskService, permissions_for_role
from cnb_contracts import (
    AdminRoleListResponse,
    AdminSessionResponse,
    AgentLifecycleImpactResponse,
    AlertPolicySimulationResponse,
    ApiErrorResponse,
    BootstrapSettingsResponse,
    ChannelAlertLifecycleListResponse,
    ChannelAlertLifecycleMetricsResponse,
    ChannelOperationMetricsListResponse,
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
    EvaluationComparisonResponse,
    EvaluationModelTargetListResponse,
    EvaluationReportResponse,
    EvaluationRunResponse,
    EvaluationSuiteResponse,
    HealthResponse,
    ManagedAdminSessionResponse,
    ManagedUserDetailResponse,
    MessageAcceptedResponse,
    MessageFeedbackResponse,
    MessageListResponse,
    MessageSearchResponse,
    ObservabilityAlertBatchDispositionResponse,
    ObservabilityAlertDispositionEventResponse,
    ObservabilityAlertDispositionResponse,
    ObservabilityAlertLifecycleMetricsResponse,
    ObservabilityAlertLifecyclePageResponse,
    ObservabilityAlertLifecycleResponse,
    ObservabilityAlertReplayMetricsResponse,
    ObservabilityAlertReplayReviewPageResponse,
    ObservabilityDashboardResponse,
    SystemOverviewResponse,
    TaskStatusResponse,
)
from cnb_domain import (
    ActiveAlert,
    AdminPrincipal,
    AdminRole,
    AlertSeverity,
    BackgroundJobKind,
    ChannelAlert,
    ChannelAlertLifecycleStatus,
    DevelopmentIdentity,
    JsonValue,
    ManagedAdminSession,
    ObservabilityAlertReplayDecision,
    ObservabilityAlertReplayReason,
    ObservabilityAlertReplayReview,
)
from cnb_infrastructure import (
    InMemoryMemoryRepository,
    InMemoryTaskRepository,
    MemoryAdministrationRepository,
    MemoryAttachmentRepository,
    MemoryChannelRepository,
    MemoryConfigurationRepository,
    MemoryConversationRepository,
    MemoryDataLifecycleRepository,
    MemoryObjectStorage,
    MemoryObservabilityRepository,
    Settings,
)


class ApiTelegramTransport:
    """API 级测试使用的单响应 Telegram Transport。"""

    def __init__(self, response: Response) -> None:
        self.response = response
        self.requests: list[tuple[str, Mapping[str, object] | None]] = []

    async def post(
        self,
        url: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> Response:
        self.requests.append((url, json))
        return self.response

    async def aclose(self) -> None:
        """注入 Transport 不持有连接池。"""


async def test_observability_dashboard_records_only_safe_request_metadata() -> None:
    repository = MemoryObservabilityRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        observability_repository=repository,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/v1/observability/dashboard?secret=must-not-be-recorded")
        response = await client.get("/api/v1/observability/dashboard")

    payload = ObservabilityDashboardResponse.model_validate(response.json())
    assert response.status_code == 200
    assert payload.api.requests == 1
    assert payload.api.server_errors == 0
    assert payload.models == ()
    assert payload.channel_delivery.attempts == 0
    assert payload.channel_delivery.failure_rate_percent == 0
    assert payload.notification_delivery.total == 0
    assert payload.notification_delivery.dead_letters == 0


async def test_observability_alert_lifecycle_api_filters_and_manages_dispositions() -> None:
    """通用告警 API 覆盖组合筛选、权限、处置与 Agent 隔离。"""
    repository = MemoryObservabilityRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        observability_repository=repository,
    )
    identity = cast(DevelopmentIdentity, app.state.development_identity)
    now = datetime.now(UTC).replace(microsecond=0)
    own_alert = ActiveAlert(
        code="model_error_rate",
        severity=AlertSeverity.CRITICAL,
        title="模型失败率过高",
        summary="仅用于 API 契约测试的安全摘要",
        current_value=12.0,
        threshold_value=5.0,
        unit="%",
        source_type="model_runtime",
        source_key="provider-a:model-a",
        first_occurred_at=now - timedelta(minutes=90),
        last_occurred_at=now,
    )
    other_source_alert = ActiveAlert(
        code="queue_backlog",
        severity=AlertSeverity.WARNING,
        title="队列积压",
        summary="仅用于筛选排除的安全摘要",
        current_value=20.0,
        threshold_value=10.0,
        unit="jobs",
        source_type="task_queue",
        source_key="default",
        first_occurred_at=now - timedelta(minutes=10),
        last_occurred_at=now,
    )
    own_reconciliation = await repository.reconcile_observability_alert_lifecycles(
        tenant_id=identity.tenant_id,
        agent_id=identity.agent_id,
        alerts=(own_alert, other_source_alert),
        observed_at=now,
    )
    lifecycle_id = next(
        item.id
        for item in own_reconciliation.active_lifecycles
        if item.source_key == own_alert.alert_key
    )
    lifecycle_ids = tuple(item.id for item in own_reconciliation.active_lifecycles)
    await repository.record_observability_alert_replay_review(
        ObservabilityAlertReplayReview(
            id=uuid4(),
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            source_job_id=uuid4(),
            source_type="model_runtime",
            source_key=own_alert.alert_key,
            decision=ObservabilityAlertReplayDecision.BLOCKED,
            reason_code=ObservabilityAlertReplayReason.BLOCKED_ACTIVE_SUPPRESSION,
            actor_id=identity.user_id,
            suppression_expires_at=now + timedelta(hours=1),
            reviewed_at=now,
        )
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        filtered_response = await client.get(
            "/api/v1/observability/alert-lifecycles",
            params={
                "status": "active",
                "source_type": "model_runtime",
                "severity": "critical",
                "minimum_duration_minutes": 60,
            },
            headers={"X-CNB-Development-Role": "viewer"},
        )
        metrics_response = await client.get(
            "/api/v1/observability/alert-lifecycles/metrics",
            params={
                "window_minutes": 1_440,
                "bucket_minutes": 60,
                "source_type": "model_runtime",
                "severity": "critical",
            },
            headers={"X-CNB-Development-Role": "viewer"},
        )
        page_response = await client.get(
            "/api/v1/observability/alert-lifecycles/page",
            params={"source_type": "model_runtime", "limit": 1},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        invalid_page_response = await client.get(
            "/api/v1/observability/alert-lifecycles/page",
            params={"cursor": "invalid"},
        )
        replay_metrics_response = await client.get(
            "/api/v1/observability/alert-replay-reviews/metrics",
            params={"source_type": "model_runtime"},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        replay_reviews_response = await client.get(
            "/api/v1/observability/alert-replay-reviews",
            params={"decision": "blocked", "limit": 1},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        viewer_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{lifecycle_id}/acknowledge",
            json={"reason": "值班人员已接手", "confirmed": True},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        unconfirmed_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{lifecycle_id}/acknowledge",
            json={"reason": "未显式确认", "confirmed": False},
            headers={"X-CNB-Development-Role": "operator"},
        )
        acknowledged_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{lifecycle_id}/acknowledge",
            json={"reason": " 值班人员已接手 ", "confirmed": True},
            headers={"X-CNB-Development-Role": "operator"},
        )
        suppressed_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{lifecycle_id}/suppress",
            json={
                "reason": "计划内维护窗口",
                "expires_at": (now + timedelta(hours=2)).isoformat(),
                "confirmed": True,
            },
            headers={"X-CNB-Development-Role": "operator"},
        )
        disposed_list_response = await client.get(
            "/api/v1/observability/alert-lifecycles",
            params={"source_type": "model_runtime"},
        )
        cleared_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{lifecycle_id}/clear-disposition",
            json={"confirmed": True},
            headers={"X-CNB-Development-Role": "operator"},
        )
        batch_response = await client.post(
            "/api/v1/observability/alert-lifecycles/batch-disposition",
            json={
                "lifecycle_ids": [str(item) for item in lifecycle_ids],
                "action": "acknowledge",
                "reason": "批量值班确认",
                "confirmed": True,
            },
            headers={"X-CNB-Development-Role": "operator"},
        )
        history_response = await client.get(
            "/api/v1/observability/alert-disposition-events",
            params={"lifecycle_id": str(lifecycle_id), "limit": 20},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        missing_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{uuid4()}/acknowledge",
            json={"reason": "不存在的生命周期", "confirmed": True},
            headers={"X-CNB-Development-Role": "operator"},
        )
        other_agent_response = await client.post(
            "/api/v1/administration/agents",
            json={"name": "通用告警隔离伙伴"},
        )
        cross_agent_response = await client.post(
            f"/api/v1/observability/alert-lifecycles/{lifecycle_id}/acknowledge",
            json={"reason": "不得跨 Agent 处置", "confirmed": True},
            headers={
                "X-CNB-Development-Role": "operator",
                "X-CNB-Agent-ID": other_agent_response.json()["id"],
            },
        )

    filtered = tuple(
        ObservabilityAlertLifecycleResponse.model_validate(item)
        for item in filtered_response.json()
    )
    metrics = ObservabilityAlertLifecycleMetricsResponse.model_validate(metrics_response.json())
    page = ObservabilityAlertLifecyclePageResponse.model_validate(page_response.json())
    replay_metrics = ObservabilityAlertReplayMetricsResponse.model_validate(
        replay_metrics_response.json()
    )
    replay_reviews = ObservabilityAlertReplayReviewPageResponse.model_validate(
        replay_reviews_response.json()
    )
    acknowledged = ObservabilityAlertDispositionResponse.model_validate(
        acknowledged_response.json()
    )
    suppressed = ObservabilityAlertDispositionResponse.model_validate(suppressed_response.json())
    disposed = tuple(
        ObservabilityAlertLifecycleResponse.model_validate(item)
        for item in disposed_list_response.json()
    )
    cleared = ObservabilityAlertDispositionResponse.model_validate(cleared_response.json())
    batch = ObservabilityAlertBatchDispositionResponse.model_validate(batch_response.json())
    history = tuple(
        ObservabilityAlertDispositionEventResponse.model_validate(item)
        for item in history_response.json()
    )
    assert filtered_response.status_code == 200
    assert [item.id for item in filtered] == [lifecycle_id]
    assert metrics_response.status_code == 200
    assert (metrics.active, metrics.opened, metrics.resolved, metrics.escalated) == (1, 1, 0, 0)
    assert [item.source_type for item in metrics.sources] == ["model_runtime"]
    assert len(metrics.trend) == 24
    assert page_response.status_code == 200
    assert [item.id for item in page.items] == [lifecycle_id]
    assert page.next_cursor is None
    assert invalid_page_response.status_code == 422
    assert replay_metrics_response.status_code == 200
    assert (replay_metrics.total, replay_metrics.allowed, replay_metrics.blocked) == (1, 0, 1)
    assert replay_metrics.allowed_rate_percent == 0
    assert replay_reviews_response.status_code == 200
    assert len(replay_reviews.items) == 1
    assert replay_reviews.items[0].reason_code == "blocked_active_suppression"
    assert viewer_response.status_code == 403
    assert unconfirmed_response.status_code == 422
    assert acknowledged.status == "acknowledged"
    assert acknowledged.reason == "值班人员已接手"
    assert suppressed.status == "suppressed"
    assert disposed[0].disposition_status == "suppressed"
    assert disposed[0].disposition_reason == "计划内维护窗口"
    assert cleared.status == "cleared"
    assert batch_response.status_code == 200
    assert {item.lifecycle_id for item in batch.items} == set(lifecycle_ids)
    assert history_response.status_code == 200
    assert [item.action for item in history] == [
        "acknowledged",
        "cleared",
        "suppressed",
        "acknowledged",
    ]
    assert missing_response.status_code == 404
    assert cross_agent_response.status_code == 404


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
    assert overview.policy.deleted_agent_days == 30
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
        administration_repository=MemoryAdministrationRepository(
            DevelopmentIdentity(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                user_name=principal.display_name,
                agent_name="OIDC 测试 Agent",
            )
        ),
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
        administration_repository=MemoryAdministrationRepository(
            DevelopmentIdentity(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_id=agent_id,
                user_name=principal.display_name,
                agent_name="OIDC WebSocket Agent",
            )
        ),
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


async def test_multi_model_comparison_api_exposes_governed_targets_and_isolated_detail() -> None:
    """对比 API 只接收已发布路由档案，列表不携带回答，详情按 Agent 隔离。"""
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        references: list[dict[str, object]] = []
        for key, model in (("fast", "friendly-fast-v1"), ("quality", "friendly-quality-v1")):
            draft_response = await client.post(
                "/api/v1/cognition/resources",
                json={
                    "kind": "model_profile",
                    "key": key,
                    "name": f"{key} 对比档案",
                    "payload": {
                        "provider": "development",
                        "model": model,
                        "purposes": ["chat.realizer"],
                        "pricing": {
                            "input_usd_per_million_tokens": 1,
                            "output_usd_per_million_tokens": 2,
                        },
                    },
                },
            )
            assert draft_response.status_code == 201
            published_response = await client.post(
                f"/api/v1/cognition/resources/{draft_response.json()['id']}/publish"
            )
            assert published_response.status_code == 200
            references.append({"key": key, "version": published_response.json()["version"]})

        route_draft = await client.post(
            "/api/v1/cognition/resources",
            json={
                "kind": "model_route",
                "key": "chat.realizer",
                "name": "同源对比路由",
                "payload": {
                    "purpose": "chat.realizer",
                    "primary_profile": references[0],
                    "fallback_profiles": references[1:],
                    "timeout_seconds": 30,
                    "max_attempts": 2,
                },
            },
        )
        assert route_draft.status_code == 201
        route_publish = await client.post(
            f"/api/v1/cognition/resources/{route_draft.json()['id']}/publish"
        )
        assert route_publish.status_code == 200

        targets_response = await client.get("/api/v1/evaluations/comparison-targets")
        comparison_response = await client.post(
            "/api/v1/evaluations/comparisons",
            json={"profile_keys": ["fast", "quality"]},
        )
        comparison = EvaluationComparisonResponse.model_validate(comparison_response.json())
        history_response = await client.get("/api/v1/evaluations/comparisons")
        detail_response = await client.get(f"/api/v1/evaluations/comparisons/{comparison.id}")
        invalid_target = await client.post(
            "/api/v1/evaluations/comparisons",
            json={"profile_keys": ["fast", "unpublished"]},
        )
        second_agent_response = await client.post(
            "/api/v1/administration/agents",
            json={"name": "隔离验证 Agent"},
        )
        isolated_detail = await client.get(
            f"/api/v1/evaluations/comparisons/{comparison.id}",
            headers={"X-CNB-Agent-ID": second_agent_response.json()["id"]},
        )
        viewer_targets = await client.get(
            "/api/v1/evaluations/comparison-targets",
            headers={"X-CNB-Development-Role": "viewer"},
        )

    targets = EvaluationModelTargetListResponse.model_validate(targets_response.json())
    assert [(item.profile_key, item.model) for item in targets.items] == [
        ("fast", "friendly-fast-v1"),
        ("quality", "friendly-quality-v1"),
    ]
    assert comparison_response.status_code == 201
    assert len(comparison.entries) == 2
    assert all(len(entry.run.results) == 5 for entry in comparison.entries)
    assert all(entry.run.model_route_version == 1 for entry in comparison.entries)
    assert history_response.status_code == 200
    assert "candidate_response" not in history_response.text
    assert history_response.json()["items"][0]["id"] == str(comparison.id)
    assert len(detail_response.json()["entries"][0]["run"]["results"]) == 5
    assert invalid_target.status_code == 422
    assert second_agent_response.status_code == 201
    assert isolated_detail.status_code == 404
    assert viewer_targets.status_code == 403


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


async def test_user_identity_detail_and_admin_session_revoke_are_safe_and_tenant_scoped() -> None:
    identity = DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.user"),
        agent_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.agent"),
        user_name="本地开发者",
        agent_name="赛博网友",
    )
    repository = MemoryAdministrationRepository(identity)
    now = datetime.now(UTC)
    session = ManagedAdminSession(
        id=uuid4(),
        external_identity_id=uuid4(),
        issued_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        revoked_at=None,
    )
    repository.seed_admin_session(identity.user_id, session)
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        administration_repository=repository,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        detail_response = await client.get(f"/api/v1/administration/users/{identity.user_id}")
        cross_tenant_user = await client.get(f"/api/v1/administration/users/{uuid4()}")
        invisible_session = await client.post(
            f"/api/v1/administration/users/{uuid4()}/sessions/{session.id}/revoke",
            json={"confirmation": f"确认撤销管理会话 {session.id}"},
        )
        invalid_confirmation = await client.post(
            f"/api/v1/administration/users/{identity.user_id}/sessions/{session.id}/revoke",
            json={"confirmation": "确认撤销"},
        )
        viewer_revoke = await client.post(
            f"/api/v1/administration/users/{identity.user_id}/sessions/{session.id}/revoke",
            json={"confirmation": f"确认撤销管理会话 {session.id}"},
            headers={"X-CNB-Development-Role": "viewer"},
        )
        revoked_response = await client.post(
            f"/api/v1/administration/users/{identity.user_id}/sessions/{session.id}/revoke",
            json={"confirmation": f"确认撤销管理会话 {session.id}"},
        )
        repeated_revoke = await client.post(
            f"/api/v1/administration/users/{identity.user_id}/sessions/{session.id}/revoke",
            json={"confirmation": f"确认撤销管理会话 {session.id}"},
        )
        audit_response = await client.get(
            "/api/v1/administration/audit",
            params={"action": "admin_session.revoked"},
        )

    detail = ManagedUserDetailResponse.model_validate(detail_response.json())
    revoked = ManagedAdminSessionResponse.model_validate(revoked_response.json())
    serialized = f"{detail_response.text}{revoked_response.text}{audit_response.text}".lower()
    assert detail_response.status_code == 200
    assert detail.tenant.name == "本地开发环境"
    assert detail.role_assignment is not None
    assert detail.role_assignment.source == "development"
    assert detail.external_identities == ()
    assert detail.admin_sessions[0].id == session.id
    assert cross_tenant_user.status_code == 404
    assert invisible_session.status_code == 404
    assert invalid_confirmation.status_code == 422
    assert viewer_revoke.status_code == 403
    assert revoked.revoked_at is not None
    assert repeated_revoke.status_code == 409
    assert audit_response.json()["items"][0]["resource_id"] == str(session.id)
    assert "token_hash" not in serialized
    assert "access_token" not in serialized


async def test_user_access_policy_and_role_override_api_enforce_permissions_and_lockout() -> None:
    identity = DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.user"),
        agent_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.agent"),
        user_name="本地开发者",
        agent_name="赛博网友",
    )
    repository = MemoryAdministrationRepository(identity)
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        administration_repository=repository,
    )
    role_confirmation = f"确认覆盖用户角色 {identity.user_id}"
    revoke_confirmation = f"确认撤销用户角色覆盖 {identity.user_id}"
    policy_confirmation = f"确认更新用户访问策略 {identity.user_id}"
    missing_user_id = uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        operator_override = await client.put(
            f"/api/v1/administration/users/{identity.user_id}/role-override",
            headers={"X-CNB-Development-Role": "operator"},
            json={"role": "admin", "confirmation": role_confirmation},
        )
        overridden = await client.put(
            f"/api/v1/administration/users/{identity.user_id}/role-override",
            json={"role": "admin", "confirmation": role_confirmation},
        )
        revoked = await client.post(
            f"/api/v1/administration/users/{identity.user_id}/role-override/revoke",
            json={"confirmation": revoke_confirmation},
        )
        missing_user = await client.patch(
            f"/api/v1/administration/users/{missing_user_id}/access-policy",
            json={
                "request_rate_limit_per_minute": 30,
                "confirmation": f"确认更新用户访问策略 {missing_user_id}",
            },
        )
        suspended_until = datetime.now(UTC) + timedelta(hours=1)
        suspended = await client.patch(
            f"/api/v1/administration/users/{identity.user_id}/access-policy",
            json={
                "request_rate_limit_per_minute": 30,
                "suspended_until": suspended_until.isoformat(),
                "suspension_reason": "安全处置",
                "confirmation": policy_confirmation,
            },
        )
        denied_after_suspension = await client.get("/api/v1/administration/session")

    assert operator_override.status_code == 403
    assert overridden.status_code == 200
    assert overridden.json()["source"] == "manual"
    assert overridden.json()["trusted_role"] == "admin"
    assert revoked.status_code == 200
    assert revoked.json()["source"] == "development"
    assert missing_user.status_code == 404
    assert suspended.status_code == 200
    assert suspended.json()["suspension_reason"] == "安全处置"
    assert denied_after_suspension.status_code == 403
    serialized = f"{overridden.text}{revoked.text}{suspended.text}".lower()
    assert "access_token" not in serialized
    assert "token_hash" not in serialized


async def test_user_rate_limit_counts_once_per_request_and_returns_retry_after() -> None:
    identity = DevelopmentIdentity(
        tenant_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.tenant"),
        user_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.user"),
        agent_id=uuid5(NAMESPACE_DNS, "cyber-netizen.local.agent"),
        user_name="本地开发者",
        agent_name="赛博网友",
    )
    repository = MemoryAdministrationRepository(identity)
    await repository.update_user_access_policy(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        request_rate_limit_per_minute=1,
        suspended_until=None,
        suspension_reason=None,
        actor_id=identity.user_id,
        updated_at=datetime.now(UTC),
    )
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        administration_repository=repository,
    )
    headers = {"Origin": "http://localhost:5173"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.get("/api/v1/administration/session", headers=headers)
        limited = await client.get("/api/v1/administration/session", headers=headers)

    assert allowed.status_code == 200
    assert limited.status_code == 429
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert "Retry-After" in limited.headers["access-control-expose-headers"]


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


async def test_agent_rename_preview_archive_and_soft_delete_flow() -> None:
    async with AsyncClient(transport=_transport(), base_url="http://test") as client:
        source_agent_id = (await client.get("/api/v1/chat/identity")).json()["agent_id"]
        blocked_archive = await client.post(
            f"/api/v1/administration/agents/{source_agent_id}/archive",
            json={"confirmation": f"确认归档智能体 {source_agent_id}"},
        )
        replacement = await client.post(
            "/api/v1/administration/agents",
            json={"name": "生命周期替代伙伴"},
        )
        rename = await client.patch(
            f"/api/v1/administration/agents/{source_agent_id}",
            json={"name": "长期伙伴"},
        )
        preview_response = await client.get(
            f"/api/v1/administration/agents/{source_agent_id}/impact"
        )
        preview = AgentLifecycleImpactResponse.model_validate(preview_response.json())
        wrong_confirmation = await client.post(
            f"/api/v1/administration/agents/{source_agent_id}/archive",
            json={"confirmation": "确认归档"},
        )
        archived = await client.post(
            f"/api/v1/administration/agents/{source_agent_id}/archive",
            json={"confirmation": preview.archive_confirmation},
        )
        archived_selection = await client.get(
            "/api/v1/chat/identity",
            headers={"X-CNB-Agent-ID": source_agent_id},
        )
        archived_preview_response = await client.get(
            f"/api/v1/administration/agents/{source_agent_id}/impact"
        )
        archived_preview = AgentLifecycleImpactResponse.model_validate(
            archived_preview_response.json()
        )
        deleted = await client.post(
            f"/api/v1/administration/agents/{source_agent_id}/delete",
            json={"confirmation": archived_preview.delete_confirmation},
        )
        audit = await client.get(
            "/api/v1/administration/audit",
            params={"action": "agent.soft_deleted"},
        )

    assert blocked_archive.status_code == 409
    assert replacement.status_code == 201
    assert rename.json()["name"] == "长期伙伴"
    assert preview.can_archive is True
    assert preview.can_delete is False
    assert preview.deleted_agent_retention_days == 30
    assert preview.counts.total == 0
    assert wrong_confirmation.status_code == 422
    assert archived.json()["status"] == "archived"
    assert archived.json()["archived_at"] is not None
    assert archived_selection.status_code == 404
    assert archived_preview.can_delete is True
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["deleted_at"] is not None
    assert deleted.json()["purge_after"] is not None
    assert audit.json()["items"][0]["detail"]["physical_delete_performed"] is False


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
    async def unexpected_probe(_: Settings) -> tuple[ComponentHealth, ...]:
        raise AssertionError("深度检查关闭时不应访问外部依赖")

    async with AsyncClient(
        transport=_transport(dependency_probe=unexpected_probe),
        base_url="http://test",
    ) as client:
        overview_response = await client.get("/api/v1/system/overview")
        registry_response = await client.get("/api/v1/configuration/definitions")

    overview = SystemOverviewResponse.model_validate(overview_response.json())
    registry = ConfigRegistryResponse.model_validate(registry_response.json())

    assert overview.configuration_definitions == len(registry.definitions)
    assert overview.environment == "test"
    assert overview.components[0] == ComponentHealth(name="api", status="healthy")
    assert [component.status for component in overview.components[1:]] == [
        "not_checked",
        "not_checked",
        "not_checked",
    ]
    assert all(component.detail for component in overview.components[1:])


async def test_system_overview_uses_real_dependency_probe_when_enabled() -> None:
    probes = 0

    async def mixed_probe(_: Settings) -> tuple[ComponentHealth, ...]:
        nonlocal probes
        probes += 1
        return (
            ComponentHealth(name="postgresql", status="healthy"),
            ComponentHealth(name="redis", status="degraded", detail="ConnectionError"),
            ComponentHealth(name="object_storage", status="healthy"),
        )

    async with AsyncClient(
        transport=_transport(deep_checks=True, dependency_probe=mixed_probe),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/system/overview")

    overview = SystemOverviewResponse.model_validate(response.json())
    assert response.status_code == 200
    assert probes == 1
    assert [(component.name, component.status) for component in overview.components] == [
        ("api", "healthy"),
        ("postgresql", "healthy"),
        ("redis", "degraded"),
        ("object_storage", "healthy"),
    ]
    assert overview.components[2].detail == "ConnectionError"


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
        default_agent_id = (await client.get("/api/v1/chat/identity")).json()["agent_id"]
        other_agent = await client.post(
            "/api/v1/administration/agents",
            json={"name": "渠道隔离伙伴"},
        )
        other_agent_id = other_agent.json()["id"]
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
        other_web = await client.post(
            "/api/v1/channels",
            headers={"X-CNB-Agent-ID": other_agent_id},
            json={
                "name": "内部 Web",
                "platform": "web",
                "status": "disabled",
                "rate_limit_per_minute": 10,
                "settings": {"audience": "isolated"},
            },
        )
        default_channels = await client.get("/api/v1/channels")
        other_channels = await client.get(
            "/api/v1/channels",
            headers={"X-CNB-Agent-ID": other_agent_id},
        )
        cross_agent_channel = await client.get(
            f"/api/v1/channels/{web_id}",
            headers={"X-CNB-Agent-ID": other_agent_id},
        )
        cross_agent_update = await client.patch(
            f"/api/v1/channels/{web_id}",
            headers={"X-CNB-Agent-ID": other_agent_id},
            json={"status": "disabled", "confirmed": True},
        )
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
        cross_agent_credential = await client.put(
            f"/api/v1/channels/{feishu_id}/credential",
            headers={"X-CNB-Agent-ID": other_agent_id},
            json={"credential": "禁止写入其他 Agent"},
        )
        tested = await client.post(f"/api/v1/channels/{feishu_id}/connection-test")
        placeholder_delivery = await client.post(
            f"/api/v1/channels/{feishu_id}/deliveries",
            json={
                **delivery_command,
                "idempotency_key": "api:feishu:delivery:1",
            },
        )
        events = await client.get("/api/v1/channels/diagnostics/events", params={"limit": 100})
        other_events = await client.get(
            "/api/v1/channels/diagnostics/events",
            params={"limit": 100},
            headers={"X-CNB-Agent-ID": other_agent_id},
        )
        cross_agent_events = await client.get(
            "/api/v1/channels/diagnostics/events",
            params={"channel_id": web_id, "limit": 100},
            headers={"X-CNB-Agent-ID": other_agent_id},
        )
        metrics = await client.get(
            "/api/v1/channels/operations/metrics",
            params={"window_minutes": 60},
        )
        web_metrics = await client.get(
            "/api/v1/channels/operations/metrics",
            params={"channel_id": web_id, "window_minutes": 60},
        )
        other_metrics = await client.get(
            "/api/v1/channels/operations/metrics",
            params={"channel_id": web_id, "window_minutes": 60},
            headers={"X-CNB-Agent-ID": other_agent_id},
        )
        invalid_metrics_window = await client.get(
            "/api/v1/channels/operations/metrics",
            params={"window_minutes": 4},
        )
        notification_timeline = await client.get(
            "/api/v1/channels/operations/alerts/notifications",
            params={"status": "succeeded", "event": "active", "limit": 20},
        )
        invalid_notification_status = await client.get(
            "/api/v1/channels/operations/alerts/notifications",
            params={"status": "unknown"},
        )
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
    assert {
        item["platform"]: item["implementation_status"] for item in catalog.json()["items"]
    } == {
        "web": "ready",
        "feishu": "placeholder",
        "discord": "placeholder",
        "telegram": "ready",
    }
    assert model_capabilities.json()["items"][1]["document_input"] is True
    assert web.status_code == 201
    assert web.json()["agent_id"] == default_agent_id
    assert other_web.status_code == 201
    assert other_web.json()["agent_id"] == other_agent_id
    assert [item["id"] for item in default_channels.json()["items"]] == [web_id]
    assert [item["id"] for item in other_channels.json()["items"]] == [other_web.json()["id"]]
    assert cross_agent_channel.status_code == 404
    assert cross_agent_update.status_code == 404
    assert cross_agent_credential.status_code == 404
    assert cross_agent_events.status_code == 404
    metrics_payload = ChannelOperationMetricsListResponse.model_validate(metrics.json())
    web_metrics_payload = ChannelOperationMetricsListResponse.model_validate(web_metrics.json())
    assert metrics.status_code == web_metrics.status_code == 200
    assert {item.channel_id for item in metrics_payload.items} == {UUID(web_id), UUID(feishu_id)}
    assert len(web_metrics_payload.items) == 1
    assert web_metrics_payload.items[0].inbound_events == 1
    assert notification_timeline.status_code == 200
    assert notification_timeline.json() == {
        "total": 0,
        "pending": 0,
        "running": 0,
        "retrying": 0,
        "succeeded": 0,
        "failed": 0,
        "dead_letters": 0,
        "current_consecutive_failures": 0,
        "last_succeeded_at": None,
        "items": [],
    }
    assert invalid_notification_status.status_code == 422
    assert web_metrics_payload.items[0].outbound_events == 1
    assert web_metrics_payload.items[0].outbound_delivered == 1
    assert web_metrics_payload.items[0].outbound_failure_rate_percent == 0
    assert other_metrics.status_code == 404
    assert invalid_metrics_window.status_code == 422
    assert "你好，**渠道**" not in metrics.text
    assert "不能通过响应返回的渠道密钥" not in metrics.text
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
    assert other_events.json()["items"] == []
    assert viewer_create.status_code == 403


async def test_channel_alert_lifecycle_api_is_filtered_isolated_and_safe() -> None:
    channel_repository = MemoryChannelRepository()
    task_repository = InMemoryTaskRepository()
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        channel_repository=channel_repository,
        conversation_repository=MemoryConversationRepository(),
        task_repository=task_repository,
    )
    identity = cast(DevelopmentIdentity, app.state.development_identity)
    now = datetime.now(UTC).replace(microsecond=0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        other_agent = await client.post(
            "/api/v1/administration/agents",
            json={"name": "生命周期隔离伙伴"},
        )
        other_agent_id = UUID(other_agent.json()["id"])
        policy_draft_response = await client.post(
            "/api/v1/configuration/drafts",
            json={
                "note": "告警策略 Agent 隔离测试",
                "values": [
                    {
                        "key": "alerts.notification.escalation_level_1_adapter",
                        "scope_type": "agent",
                        "scope_id": str(identity.agent_id),
                        "value": "feishu_webhook",
                    },
                    {
                        "key": "alerts.notification.escalation_level_1_adapter",
                        "scope_type": "agent",
                        "scope_id": str(other_agent_id),
                        "value": "email",
                    },
                    {
                        "key": "alerts.notification.webhook_url",
                        "scope_type": "agent",
                        "scope_id": str(identity.agent_id),
                        "value": "https://private-alert-target.example/hook",
                    },
                ],
            },
        )
        policy_draft = ConfigVersionResponse.model_validate(policy_draft_response.json())
        await client.post(f"/api/v1/configuration/versions/{policy_draft.id}/publish")
        own_channel = await client.post(
            "/api/v1/channels",
            json={
                "name": "生命周期 Web",
                "platform": "web",
                "status": "enabled",
                "rate_limit_per_minute": 60,
            },
        )
        other_channel = await client.post(
            "/api/v1/channels",
            headers={"X-CNB-Agent-ID": str(other_agent_id)},
            json={
                "name": "隔离生命周期 Web",
                "platform": "web",
                "status": "enabled",
                "rate_limit_per_minute": 60,
            },
        )
        own_channel_id = UUID(own_channel.json()["id"])
        other_channel_id = UUID(other_channel.json()["id"])
        active_alert = ChannelAlert(
            channel_id=own_channel_id,
            code="channel_error_rate",
            error_code="web_http_503",
            severity=AlertSeverity.CRITICAL,
            title="渠道错误率过高",
            summary="仅用于仓储构造的安全聚合摘要",
            occurrences=4,
            current_value=20.0,
            threshold_value=5.0,
            unit="%",
            first_occurred_at=now - timedelta(minutes=40),
            last_occurred_at=now - timedelta(minutes=10),
            cooldown_until=now + timedelta(minutes=20),
        )
        resolved_alert = ChannelAlert(
            channel_id=own_channel_id,
            code="channel_health_degraded",
            error_code=None,
            severity=AlertSeverity.WARNING,
            title="渠道健康状态持续降级",
            summary="不得通过生命周期接口返回的内部摘要",
            occurrences=2,
            current_value=30.0,
            threshold_value=15.0,
            unit="minutes",
            first_occurred_at=now - timedelta(minutes=35),
            last_occurred_at=now - timedelta(minutes=10),
            cooldown_until=now + timedelta(minutes=20),
        )
        other_alert = ChannelAlert(
            channel_id=other_channel_id,
            code="channel_error_rate",
            error_code="isolated_http_500",
            severity=AlertSeverity.CRITICAL,
            title="隔离渠道错误率过高",
            summary="其他 Agent 的安全聚合摘要",
            occurrences=3,
            current_value=15.0,
            threshold_value=5.0,
            unit="%",
            first_occurred_at=now - timedelta(minutes=25),
            last_occurred_at=now - timedelta(minutes=10),
            cooldown_until=now + timedelta(minutes=20),
        )
        opened = await channel_repository.reconcile_alert_lifecycles(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            alerts=(active_alert, resolved_alert),
            observed_at=now - timedelta(minutes=10),
        )
        active_lifecycle = next(item for item in opened if item.alert_key == active_alert.alert_key)
        await channel_repository.reconcile_alert_lifecycles(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            alerts=(active_alert,),
            observed_at=now - timedelta(minutes=5),
        )
        await channel_repository.mark_alert_lifecycles_escalated(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            lifecycle_ids=(active_lifecycle.id,),
            escalation_level=1,
            escalated_at=now - timedelta(minutes=3),
        )
        await channel_repository.reconcile_alert_lifecycles(
            tenant_id=identity.tenant_id,
            agent_id=other_agent_id,
            alerts=(other_alert,),
            observed_at=now - timedelta(minutes=5),
        )
        notification_payload: dict[str, JsonValue] = {
            "agent_id": str(identity.agent_id),
            "adapter": "webhook",
            "delivery_payload": {
                "event": "channel.alerts.escalated",
                "alert_count": 1,
                "alerts": [{"alert_key": active_alert.alert_key}],
            },
            "idempotency_key": "api-lifecycle-escalation",
        }
        await BackgroundTaskService(task_repository).enqueue(
            tenant_id=identity.tenant_id,
            agent_id=identity.agent_id,
            kind=BackgroundJobKind.NOTIFICATION_DELIVERY,
            payload=notification_payload,
            deduplication_key="api:lifecycle:escalation",
            created_by=identity.user_id,
        )

        lifecycle_response = await client.get("/api/v1/channels/operations/alerts/lifecycles")
        filtered_response = await client.get(
            "/api/v1/channels/operations/alerts/lifecycles",
            params={
                "channel_id": str(own_channel_id),
                "status": "resolved",
                "severity": "warning",
            },
        )
        cross_agent_response = await client.get(
            "/api/v1/channels/operations/alerts/lifecycles",
            params={"channel_id": str(own_channel_id)},
            headers={"X-CNB-Agent-ID": str(other_agent_id)},
        )
        metrics_response = await client.get(
            "/api/v1/channels/operations/alerts/lifecycles/metrics",
            params={"window_minutes": 60, "bucket_minutes": 5},
        )
        timeline_response = await client.get(
            "/api/v1/channels/operations/alerts/notifications",
            params={"event": "escalation", "limit": 20},
        )
        simulation_command = {
            "severity": "critical",
            "duration_minutes": 40,
            "current_level": 0,
            "evaluated_at": "2026-09-14T01:00:00Z",
        }
        simulation_response = await client.post(
            "/api/v1/channels/operations/alerts/policy/simulate",
            json=simulation_command,
        )
        other_simulation_response = await client.post(
            "/api/v1/channels/operations/alerts/policy/simulate",
            headers={"X-CNB-Agent-ID": str(other_agent_id)},
            json=simulation_command,
        )

    lifecycles = ChannelAlertLifecycleListResponse.model_validate(lifecycle_response.json())
    filtered = ChannelAlertLifecycleListResponse.model_validate(filtered_response.json())
    metrics = ChannelAlertLifecycleMetricsResponse.model_validate(metrics_response.json())
    assert lifecycle_response.status_code == filtered_response.status_code == 200
    assert len(lifecycles.items) == 2
    assert {item.status for item in lifecycles.items} == {
        ChannelAlertLifecycleStatus.ACTIVE,
        ChannelAlertLifecycleStatus.RESOLVED,
    }
    assert len(filtered.items) == 1
    assert filtered.items[0].status is ChannelAlertLifecycleStatus.RESOLVED
    assert filtered.items[0].severity is AlertSeverity.WARNING
    assert filtered.items[0].channel_id == own_channel_id
    assert cross_agent_response.status_code == 404
    assert metrics_response.status_code == 200
    assert metrics.active == 1
    assert metrics.opened == 2
    assert metrics.resolved == 1
    assert metrics.escalated == 1
    assert metrics.mean_recovery_seconds == 30 * 60
    assert metrics.p95_recovery_seconds == 30 * 60
    assert sum(item.opened for item in metrics.trend) == 2
    assert sum(item.resolved for item in metrics.trend) == 1
    assert sum(item.escalated for item in metrics.trend) == 1
    timeline = timeline_response.json()
    assert timeline_response.status_code == 200
    assert timeline["total"] == timeline["pending"] == 1
    assert len(timeline["items"]) == 1
    assert timeline["items"][0]["event"] == "escalation"
    assert timeline["items"][0]["adapter"] == "webhook"
    assert timeline["items"][0]["alert_count"] == 1
    simulation = AlertPolicySimulationResponse.model_validate(simulation_response.json())
    other_simulation = AlertPolicySimulationResponse.model_validate(
        other_simulation_response.json()
    )
    assert simulation_response.status_code == other_simulation_response.status_code == 200
    assert (simulation.target_level, simulation.adapter) == (1, "feishu_webhook")
    assert (other_simulation.target_level, other_simulation.adapter) == (1, "email")
    combined_response = (
        lifecycle_response.text
        + metrics_response.text
        + timeline_response.text
        + simulation_response.text
        + other_simulation_response.text
    )
    assert "secret" not in combined_response.casefold()
    assert "private-alert-target.example" not in combined_response
    assert other_alert.alert_key not in combined_response
    assert active_alert.alert_key not in timeline_response.text
    assert active_alert.summary not in combined_response
    assert resolved_alert.summary not in combined_response


async def test_telegram_remote_rate_limit_is_safe_and_recorded_by_api() -> None:
    token = "123456789:" + ("A" * 35)
    remote_description = "remote secret diagnostic"
    transport = ApiTelegramTransport(
        Response(
            429,
            json={
                "ok": False,
                "description": remote_description,
                "parameters": {"retry_after": 7},
            },
        )
    )
    registry = ChannelAdapterRegistry((TelegramChannelAdapter(transport=transport),))
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        channel_adapter_registry=registry,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel = await client.post(
            "/api/v1/channels",
            json={
                "name": "Telegram 限流测试",
                "platform": "telegram",
                "status": "enabled",
                "rate_limit_per_minute": 10,
                "credential": token,
            },
        )
        delivery = await client.post(
            f"/api/v1/channels/{channel.json()['id']}/deliveries",
            json={
                "recipient_id": "-1001234567890",
                "blocks": [{"kind": "text", "text": "不得进入诊断的正文"}],
                "idempotency_key": "telegram:api:rate-limit",
                "proactive": True,
            },
        )
        events = await client.get("/api/v1/channels/diagnostics/events")
        connection_test = await client.post(
            f"/api/v1/channels/{channel.json()['id']}/connection-test"
        )
        connection_events = await client.get(
            "/api/v1/channels/diagnostics/events",
            params={"event_type": "connection.tested"},
        )

    assert channel.status_code == 201
    assert delivery.status_code == 429
    assert delivery.json()["error"]["code"] == "rate_limited"
    assert delivery.json()["error"]["message"] == "Telegram 已限制发送频率"
    assert delivery.headers["retry-after"] == "7"
    assert token not in delivery.text
    assert remote_description not in delivery.text
    diagnostic = events.json()["items"][0]
    assert diagnostic["status"] == "rate_limited"
    assert diagnostic["error_code"] == "telegram_rate_limited"
    assert "不得进入诊断" not in str(diagnostic["payload_summary"])
    assert len(transport.requests) == 2
    assert transport.requests[1][0].endswith("/getMe")
    assert connection_events.status_code == 200
    assert connection_test.status_code == 200
    assert connection_test.json()["health_status"] == "degraded"
    assert remote_description not in connection_test.text
    assert len(connection_events.json()["items"]) == 1
    assert connection_events.json()["items"][0]["event_type"] == "connection.tested"


async def test_telegram_webhook_management_api_is_agent_isolated_and_safe() -> None:
    token = "123456789:" + ("A" * 35)
    webhook_secret = "api-webhook-secret_1"
    webhook_url = "https://bot.example.invalid/telegram"
    transport = ApiTelegramTransport(
        Response(
            200,
            json={
                "ok": True,
                "result": {
                    "url": webhook_url,
                    "pending_update_count": 4,
                    "last_error_date": 1_789_000_000,
                    "last_error_message": "remote secret detail",
                    "allowed_updates": ["message", "callback_query"],
                },
            },
        )
    )
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
        channel_adapter_registry=ChannelAdapterRegistry(
            (TelegramChannelAdapter(transport=transport),)
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        channel = await client.post(
            "/api/v1/channels",
            json={
                "name": "Telegram Webhook API",
                "platform": "telegram",
                "status": "enabled",
                "rate_limit_per_minute": 60,
                "settings": {},
                "credential": token,
            },
        )
        channel_id = channel.json()["id"]
        secret = await client.post(
            "/api/v1/configuration/secrets",
            json={
                "key": "telegram_webhook_secret",
                "scope_type": "channel",
                "scope_id": channel_id,
                "plaintext": webhook_secret,
            },
        )
        unconfirmed = await client.post(
            f"/api/v1/channels/{channel_id}/telegram-webhook/register",
            json={"webhook_url": webhook_url},
        )
        status_response = await client.get(f"/api/v1/channels/{channel_id}/telegram-webhook")
        registered = await client.post(
            f"/api/v1/channels/{channel_id}/telegram-webhook/register",
            json={
                "webhook_url": webhook_url,
                "drop_pending_updates": True,
                "confirmed": True,
            },
        )
        cleared = await client.post(
            f"/api/v1/channels/{channel_id}/telegram-webhook/clear",
            json={"drop_pending_updates": False, "confirmed": True},
        )
        events = await client.get(
            "/api/v1/channels/diagnostics/events", params={"channel_id": channel_id}
        )
        cross_agent = await client.get(
            f"/api/v1/channels/{channel_id}/telegram-webhook",
            headers={"X-CNB-Agent-ID": str(uuid4())},
        )

    assert channel.status_code == 201
    assert secret.status_code == 200
    assert webhook_secret not in secret.text
    assert unconfirmed.status_code == 409
    assert status_response.status_code == registered.status_code == cleared.status_code == 200
    assert status_response.json()["configured"] is True
    assert registered.json()["pending_update_count"] == 4
    assert registered.json()["allowed_updates"] == ["message"]
    assert cleared.json()["configured"] is True
    assert events.status_code == 200
    assert len(events.json()["items"]) == 2
    assert webhook_url not in status_response.text
    assert webhook_url not in registered.text
    assert webhook_secret not in registered.text
    assert token not in registered.text
    assert "remote secret detail" not in registered.text
    assert webhook_url not in events.text
    assert webhook_secret not in events.text
    assert token not in events.text
    assert cross_agent.status_code == 404
    assert [request[0].rsplit("/", 1)[-1] for request in transport.requests] == [
        "getWebhookInfo",
        "setWebhook",
        "getWebhookInfo",
        "deleteWebhook",
        "getWebhookInfo",
    ]
    assert transport.requests[1][1] == {
        "url": webhook_url,
        "secret_token": webhook_secret,
        "drop_pending_updates": True,
        "allowed_updates": ["message"],
    }
    assert transport.requests[3][1] == {"drop_pending_updates": False}


async def test_telegram_webhook_safely_routes_text_updates_to_the_inbox() -> None:
    webhook_secret = "telegram-webhook-test-secret"
    message_text = "只应进入净化入站信封的 Telegram 文本"
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        conversation = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "Telegram 入站会话"},
        )
        channel = await client.post(
            "/api/v1/channels",
            json={
                "name": "Telegram 入站",
                "platform": "telegram",
                "status": "enabled",
                "rate_limit_per_minute": 60,
                "settings": {},
            },
        )
        channel_id = channel.json()["id"]
        secret = await client.post(
            "/api/v1/configuration/secrets",
            json={
                "key": "telegram_webhook_secret",
                "scope_type": "channel",
                "scope_id": channel_id,
                "plaintext": webhook_secret,
            },
        )
        configured_channel = await client.get(f"/api/v1/channels/{channel_id}")
        identity_mapping = await client.post(
            "/api/v1/integrations/identity-mappings",
            json={
                "channel_id": channel_id,
                "external_subject_id": "501",
                "user_id": identity["user_id"],
            },
        )
        conversation_mapping = await client.post(
            "/api/v1/integrations/conversation-mappings",
            json={
                "channel_id": channel_id,
                "user_id": identity["user_id"],
                "kind": "direct",
                "external_conversation_id": "501",
                "conversation_id": conversation.json()["id"],
            },
        )
        now = datetime.now(UTC)
        update: dict[str, JsonValue] = {
            "update_id": 800_001,
            "message": {
                "message_id": 42,
                "date": int(now.timestamp()),
                "from": {"id": 501, "is_bot": False},
                "chat": {"id": 501, "type": "private"},
                "text": message_text,
            },
        }
        webhook_url = f"/api/v1/webhooks/telegram/{channel_id}"
        headers = {"X-Telegram-Bot-Api-Secret-Token": webhook_secret}
        accepted = await client.post(webhook_url, json=update, headers=headers)
        duplicate = await client.post(webhook_url, json=update, headers=headers)
        inbox = await client.get("/api/v1/integrations/inbox")
        jobs = await client.get("/api/v1/tasks/jobs")
        wrong_secret = await client.post(
            webhook_url,
            json=update,
            headers={"X-Telegram-Bot-Api-Secret-Token": "incorrect"},
        )
        no_content_type = await client.post(webhook_url, content=b"{}", headers=headers)
        malformed_json = await client.post(
            webhook_url,
            content=b"{",
            headers={**headers, "content-type": "application/json"},
        )
        unsupported_update = await client.post(
            webhook_url,
            json={"update_id": 800_002, "channel_post": {}},
            headers=headers,
        )
        unmapped_update: dict[str, JsonValue] = {
            "update_id": 800_003,
            "message": {
                "message_id": 43,
                "date": int(now.timestamp()),
                "from": {"id": 502, "is_bot": False},
                "chat": {"id": 502, "type": "private"},
                "text": message_text,
            },
        }
        unmapped = await client.post(webhook_url, json=unmapped_update, headers=headers)
        oversized = await client.post(
            webhook_url,
            content=b" " * (10 * 1024 * 1024 + 1),
            headers={**headers, "content-type": "application/json"},
        )

    assert channel.status_code == 201
    assert secret.status_code == 200
    assert webhook_secret not in secret.text
    assert configured_channel.json()["inbound_webhook_configured"] is True
    assert identity_mapping.status_code == conversation_mapping.status_code == 201
    assert accepted.status_code == duplicate.status_code == 204
    assert accepted.content == duplicate.content == b""
    assert len(inbox.json()["items"]) == len(jobs.json()["items"]) == 1
    assert inbox.json()["items"][0]["platform"] == "telegram"
    assert message_text not in inbox.text
    assert webhook_secret not in inbox.text
    assert wrong_secret.status_code == unmapped.status_code == 404
    assert channel_id not in wrong_secret.text
    assert message_text not in wrong_secret.text
    assert no_content_type.status_code == 415
    assert malformed_json.status_code == 400
    assert unsupported_update.status_code == 422
    assert oversized.status_code == 413


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


async def test_external_mapping_and_replayable_inbox_api_are_agent_isolated() -> None:
    app = create_app(
        Settings(environment="test"),
        configuration_repository=MemoryConfigurationRepository(),
        conversation_repository=MemoryConversationRepository(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.get("/api/v1/chat/identity")).json()
        conversation = await client.post(
            "/api/v1/chat/conversations",
            json={"title": "IM 路由会话"},
        )
        channel = await client.post(
            "/api/v1/channels",
            json={
                "name": "Inbox Web",
                "platform": "web",
                "status": "enabled",
                "rate_limit_per_minute": 60,
                "settings": {},
            },
        )
        channel_id = channel.json()["id"]
        identity_mapping = await client.post(
            "/api/v1/integrations/identity-mappings",
            json={
                "channel_id": channel_id,
                "external_subject_id": "web-user-1",
                "user_id": identity["user_id"],
            },
        )
        conversation_mapping = await client.post(
            "/api/v1/integrations/conversation-mappings",
            json={
                "channel_id": channel_id,
                "user_id": identity["user_id"],
                "kind": "direct",
                "external_conversation_id": "web-conversation-1",
                "conversation_id": conversation.json()["id"],
            },
        )
        now = datetime.now(UTC)
        inbound_command: dict[str, object] = {
            "payload": {
                "external_event_id": "web-event-1",
                "message_external_id": "web-message-1",
                "sender_external_id": "web-user-1",
                "conversation_external_id": "web-conversation-1",
                "conversation_kind": "direct",
                "text": "只存在于净化 Envelope 的消息",
                "occurred_at": now.isoformat(),
            },
            "signature_valid": True,
            "payload_size_bytes": 256,
            "received_at": now.isoformat(),
        }
        accepted = await client.post(
            f"/api/v1/integrations/inbound/{channel_id}/simulate",
            json=inbound_command,
        )
        retry_payload = cast(dict[str, object], inbound_command["payload"]).copy()
        retry_payload["external_event_id"] = "web-event-retry"
        duplicate = await client.post(
            f"/api/v1/integrations/inbound/{channel_id}/simulate",
            json={**inbound_command, "payload": retry_payload},
        )
        inbox = await client.get("/api/v1/integrations/inbox")
        viewer_identity_mappings = await client.get(
            "/api/v1/integrations/identity-mappings",
            headers={"X-CNB-Development-Role": "viewer"},
        )
        viewer_create = await client.post(
            "/api/v1/integrations/identity-mappings",
            headers={"X-CNB-Development-Role": "viewer"},
            json={
                "channel_id": channel_id,
                "external_subject_id": "forbidden",
                "user_id": identity["user_id"],
            },
        )
        other_agent = await client.post(
            "/api/v1/administration/agents",
            json={"name": "Inbox 隔离 Agent"},
        )
        isolated = await client.get(
            "/api/v1/integrations/inbox",
            headers={"X-CNB-Agent-ID": other_agent.json()["id"]},
        )
        cross_agent_mapping = await client.patch(
            f"/api/v1/integrations/identity-mappings/{identity_mapping.json()['id']}/status",
            headers={"X-CNB-Agent-ID": other_agent.json()["id"]},
            json={"status": "disabled", "confirmed": True},
        )

    assert identity_mapping.status_code == 201
    assert conversation_mapping.status_code == 201
    assert accepted.status_code == duplicate.status_code == 200
    assert accepted.json()["created"] is True
    assert duplicate.json()["created"] is False
    assert duplicate.json()["job_id"] == accepted.json()["job_id"]
    assert len(inbox.json()["items"]) == 1
    item = inbox.json()["items"][0]
    assert item["schema_version"] == "1"
    assert item["content_kinds"] == ["text"]
    assert item["external_subject_digest"] != "web-user-1"
    assert "只存在于净化" not in inbox.text
    assert viewer_identity_mappings.status_code == 200
    assert viewer_create.status_code == 403
    assert isolated.json()["items"] == []
    assert cross_agent_mapping.status_code == 404
