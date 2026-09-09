"""带超时边界的启动基础设施就绪探针。"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from cnb_contracts import ComponentHealth
from cnb_infrastructure.settings import Settings

DependencyProbe = Callable[[Settings], Awaitable[tuple[ComponentHealth, ...]]]


async def probe_dependencies(settings: Settings) -> tuple[ComponentHealth, ...]:
    """并发探测各项独立依赖，并为每项探针设置超时。"""
    results = await asyncio.gather(
        _safe_probe("postgresql", lambda: _probe_postgresql(settings)),
        _safe_probe("redis", lambda: _probe_redis(settings)),
        _safe_probe("object_storage", lambda: _probe_object_storage(settings)),
    )
    return tuple(results)


async def _safe_probe(name: str, probe: Callable[[], Awaitable[None]]) -> ComponentHealth:
    try:
        async with asyncio.timeout(2.5):
            await probe()
    except Exception as error:  # 就绪检查应报告服务降级，不能导致 API 崩溃。
        return ComponentHealth(name=name, status="degraded", detail=type(error).__name__)
    return ComponentHealth(name=name, status="healthy")


async def _probe_postgresql(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def _probe_redis(settings: Settings) -> None:
    # redis-py 的动态命令 API 暂未提供完整类型信息，运行时仍由真实 PING 校验。
    client = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        settings.redis_url.get_secret_value()
    )
    try:
        await client.ping()  # pyright: ignore[reportUnknownMemberType]
    finally:
        await client.aclose()


async def _probe_object_storage(settings: Settings) -> None:
    endpoint = settings.minio_endpoint_url.rstrip("/")
    async with httpx.AsyncClient(timeout=2) as client:
        response = await client.get(f"{endpoint}/minio/health/ready")
        response.raise_for_status()
