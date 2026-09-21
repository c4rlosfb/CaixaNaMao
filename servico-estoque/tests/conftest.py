"""Fixtures compartilhadas dos testes do servico-estoque.

Os testes de domínio/lock/health rodam **sem infraestrutura**: SQLite (arquivo
temporário) no lugar do PostgreSQL e fakeredis (com suporte a EVAL, para testar
o script Lua do ADR-004) no lugar do Redis real.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.locking.redis_lock import RedisLockManager
from app.models.base import Base
from app.models.item import Item


@pytest_asyncio.fixture
async def engine(tmp_path):
    """Engine SQLite em arquivo temporário (compartilhável entre sessões)."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'estoque_test.db').as_posix()}"
    eng = create_async_engine(url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def redis_fake():
    """Redis em memória que executa Lua de verdade (fakeredis[lua])."""
    cliente = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield cliente
    await cliente.aclose()


@pytest.fixture
def lock_manager(redis_fake) -> RedisLockManager:
    return RedisLockManager(redis_fake, ttl_seconds=5)


class PublisherEspiao:
    """Captura os eventos publicados no lugar do SQS."""

    def __init__(self) -> None:
        self.eventos: list[dict[str, object]] = []

    async def publicar_estoque_atualizado(self, *, evento: dict[str, object]) -> None:
        self.eventos.append(evento)


@pytest.fixture
def publisher() -> PublisherEspiao:
    return PublisherEspiao()


@pytest_asyncio.fixture
async def item_factory(session: AsyncSession):
    """Cria itens no banco de teste."""

    async def _criar(quantidade: int = 10, nome: str = "Coxinha") -> Item:
        item = Item(nome=nome, quantidade_disponivel=quantidade, quantidade_reservada=0)
        session.add(item)
        await session.commit()
        return item

    return _criar
