"""Worker 组合根及首个诊断任务。"""

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from cnb_infrastructure import get_settings

settings = get_settings()
broker = RedisBroker(url=settings.redis_url.get_secret_value())
dramatiq.set_broker(broker)


@dramatiq.actor(  # pyright: ignore[reportUnknownMemberType]
    queue_name="system", max_retries=2
)
def ping(value: str = "pong") -> dict[str, str]:
    """用于验证 Broker 与 Worker 连接的诊断 Actor。"""
    return {"status": value}
