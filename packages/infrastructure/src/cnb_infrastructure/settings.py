"""持久化运行配置可用前所需的启动设置。"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量读取的最小进程启动配置。"""

    model_config = SettingsConfigDict(
        env_prefix="CNB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://cyber_netizen:cyber_netizen@localhost:5432/cyber_netizen"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    minio_endpoint_url: str = "http://localhost:9000"
    minio_access_key: SecretStr = SecretStr("cyber-netizen")
    minio_secret_key: SecretStr = SecretStr("change-this-development-secret")
    minio_bucket: str = "cyber-netizen"
    config_master_key: SecretStr = SecretStr("development-only-placeholder")
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    readiness_deep_checks: bool = False
    authentication_mode: Literal["development", "oidc"] = "development"
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_audience: str | None = None
    oidc_scopes: tuple[str, ...] = ("openid", "profile", "email")
    oidc_tenant_id: UUID | None = None
    oidc_tenant_name: str = "Cyber Netizen"
    oidc_agent_id: UUID | None = None
    oidc_agent_name: str = "赛博网友"
    oidc_role_claim: str = "cnb_role"
    oidc_display_name_claim: str = "name"
    oidc_allowed_algorithms: tuple[Literal["RS256", "RS384", "RS512", "ES256"]] = ("RS256",)
    oidc_jwks_cache_seconds: int = 300

    @model_validator(mode="after")
    def validate_authentication_bootstrap(self) -> "Settings":
        """正式环境必须显式配置 OIDC，且发现地址不能退化为明文传输。"""
        if self.environment in {"staging", "production"} and self.authentication_mode != "oidc":
            raise ValueError("预发布与生产环境必须启用 OIDC 认证")
        if self.authentication_mode == "development":
            return self
        required = {
            "CNB_OIDC_ISSUER_URL": self.oidc_issuer_url,
            "CNB_OIDC_CLIENT_ID": self.oidc_client_id,
            "CNB_OIDC_TENANT_ID": self.oidc_tenant_id,
            "CNB_OIDC_AGENT_ID": self.oidc_agent_id,
        }
        missing = [key for key, value in required.items() if value is None or value == ""]
        if missing:
            raise ValueError(f"OIDC 启动配置缺失：{', '.join(missing)}")
        issuer = urlsplit(self.oidc_issuer_url or "")
        if issuer.scheme != "https" or not issuer.netloc:
            raise ValueError("OIDC issuer 必须是完整的 HTTPS URL")
        if issuer.query or issuer.fragment or issuer.username or issuer.password:
            raise ValueError("OIDC issuer 不能包含凭证、查询参数或片段")
        if not self.oidc_role_claim.strip() or not self.oidc_display_name_claim.strip():
            raise ValueError("OIDC claim 名称不能为空")
        if not 30 <= self.oidc_jwks_cache_seconds <= 86_400:
            raise ValueError("OIDC JWKS 缓存时间必须位于 30 到 86400 秒之间")
        if not self.oidc_allowed_algorithms:
            raise ValueError("OIDC 至少需要允许一种非对称签名算法")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """为每个进程返回一个约定不可变的设置对象。"""
    return Settings()
