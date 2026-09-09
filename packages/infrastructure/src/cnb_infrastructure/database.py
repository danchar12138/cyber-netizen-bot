"""SQLAlchemy 元数据与会话构造。"""

from collections.abc import AsyncIterator

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cnb_infrastructure.models import Base
from cnb_infrastructure.settings import Settings

__all__ = ["Base", "create_session_factory", "session_scope"]

_sqlalchemy_instrumented = False


def create_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """创建进程内异步会话工厂，但不立即建立连接。"""
    global _sqlalchemy_instrumented
    if not _sqlalchemy_instrumented:
        SQLAlchemyInstrumentor().instrument(enable_commenter=False)
        _sqlalchemy_instrumented = True
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """提供事务会话，并在失败时回滚。"""
    async with factory() as session, session.begin():
        yield session
