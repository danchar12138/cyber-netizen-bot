"""Web 管理端启动 OIDC Authorization Code + PKCE 所需的公开配置。"""

from typing import Literal

from pydantic import BaseModel


class AuthenticationConfigResponse(BaseModel):
    """不包含 client secret、API audience 或内部 claim 映射的浏览器配置。"""

    mode: Literal["development", "oidc"]
    authority: str | None
    client_id: str | None
    scope: str | None


__all__ = ["AuthenticationConfigResponse"]
