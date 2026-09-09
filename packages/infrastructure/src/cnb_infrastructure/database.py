"""SQLAlchemy metadata and session construction."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cnb_infrastructure.models import Base
from cnb_infrastructure.settings import Settings

__all__ = ["Base", "create_session_factory", "session_scope"]


def create_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Create a process-local async session factory without opening a connection."""
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a transactional session and roll back failures."""
    async with factory() as session, session.begin():
        yield session
