"""Worker composition root and first diagnostic task."""

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
    """Diagnostic actor used to verify broker and worker wiring."""
    return {"status": value}
