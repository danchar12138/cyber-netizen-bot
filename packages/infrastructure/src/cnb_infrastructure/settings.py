"""Bootstrap settings required before persisted runtime configuration is available."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Minimal process bootstrap configuration loaded from the environment."""

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
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: SecretStr = SecretStr("cyber-netizen")
    s3_secret_key: SecretStr = SecretStr("change-this-development-secret")
    s3_bucket: str = "cyber-netizen"
    config_master_key: SecretStr = SecretStr("development-only-placeholder")
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    readiness_deep_checks: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable-by-convention settings object per process."""
    return Settings()
