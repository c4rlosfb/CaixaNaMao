"""Engine e fábrica de sessões do SQLAlchemy async (pedidos_db).

A engine vive aqui (e não em `dependencies.py`) para que:

* o tamanho do pool venha da configuração (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW`), e
  não de números fixos no import;
* o shutdown da aplicação consiga fechar o pool (`dispose_engine`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def criar_engine(url: str):
    """Cria a engine com o que o dialeto aceita.

    `pool_size`/`max_overflow` existem apenas em dialetos com pool real (PostgreSQL,
    MySQL...). SQLite — usado pelo harness de testes — usa `StaticPool` e rejeita esses
    parâmetros com `TypeError`, então eles só são passados quando fazem sentido.
    """
    opcoes: dict = {"echo": False, "pool_pre_ping": True}
    if not url.startswith("sqlite"):
        opcoes["pool_size"] = settings.db_pool_size
        opcoes["max_overflow"] = settings.db_max_overflow
    return create_async_engine(url, **opcoes)


engine = criar_engine(settings.database_url)

AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Fornece uma sessão por unidade de trabalho."""
    async with AsyncSessionFactory() as session:
        yield session


async def dispose_engine() -> None:
    """Fecha o pool de conexões (chamado no shutdown da aplicação)."""
    await engine.dispose()
