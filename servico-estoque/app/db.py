"""Engine e fábrica de sessões do SQLAlchemy async (estoque_db)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Fornece uma sessão por unidade de trabalho (usado pelo health check)."""
    async with SessionFactory() as session:
        yield session
