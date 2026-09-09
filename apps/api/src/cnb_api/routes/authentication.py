"""不要求登录即可读取的安全认证引导配置。"""

from fastapi import APIRouter, Request

from cnb_contracts import AuthenticationConfigResponse
from cnb_infrastructure import Settings

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/config", response_model=AuthenticationConfigResponse)
async def authentication_config(request: Request) -> AuthenticationConfigResponse:
    """告诉 SPA 使用开发身份或 OIDC Authorization Code + PKCE。"""
    settings: Settings = request.app.state.settings
    if settings.authentication_mode == "development":
        return AuthenticationConfigResponse(
            mode="development",
            authority=None,
            client_id=None,
            scope=None,
        )
    return AuthenticationConfigResponse(
        mode="oidc",
        authority=settings.oidc_issuer_url,
        client_id=settings.oidc_client_id,
        scope=" ".join(settings.oidc_scopes),
    )
