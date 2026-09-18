"""Fixtures compartilhadas entre todos os testes do servico-pedidos."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.estoque_client import EstoqueClient, ReservaResult, ReleaseResult, StatusReserva
from app.clients.sqs_client import SQSClient
from app.dependencies import get_current_user, get_db
from app.clients.estoque_client import get_estoque_client
from app.clients.sqs_client import get_sqs_client
from app.main import app
from app.models.base import Base

# ---------------------------------------------------------------------------
# Banco em memória (SQLite async via aiosqlite)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_TestSessionFactory = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """Cria e destrói tabelas para cada teste."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with _TestSessionFactory() as session:
        yield session


# ---------------------------------------------------------------------------
# Mocks de clientes externos
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_estoque_ok() -> EstoqueClient:
    """Simula estoque com reserva sempre bem-sucedida."""
    mock = MagicMock(spec=EstoqueClient)
    mock.check_and_reserve = AsyncMock(
        return_value=ReservaResult(sucesso=True, status=StatusReserva.CONFIRMADO, mensagem="OK")
    )
    mock.release_reserva = AsyncMock(
        return_value=ReleaseResult(sucesso=True, mensagem="Liberado")
    )
    return mock


@pytest.fixture
def mock_estoque_insuficiente() -> EstoqueClient:
    """Simula estoque insuficiente."""
    mock = MagicMock(spec=EstoqueClient)
    mock.check_and_reserve = AsyncMock(
        return_value=ReservaResult(
            sucesso=False,
            status=StatusReserva.ESTOQUE_INSUFICIENTE,
            mensagem="Sem estoque",
        )
    )
    mock.release_reserva = AsyncMock(return_value=ReleaseResult(sucesso=True, mensagem="OK"))
    return mock


@pytest.fixture
def mock_sqs() -> SQSClient:
    mock = MagicMock(spec=SQSClient)
    mock.publish_pedido_criado = MagicMock()  # síncrono (fire-and-forget)
    return mock


# ---------------------------------------------------------------------------
# JWT de teste
# ---------------------------------------------------------------------------

TEST_JWT_SECRET = "secret-de-teste-nao-usar-em-producao"
TEST_VENDEDOR_ID = uuid.uuid4()


def make_test_token(
    vendedor_id: uuid.UUID = TEST_VENDEDOR_ID,
    expired: bool = False,
    secret: str = TEST_JWT_SECRET,
) -> str:
    exp = datetime.now(timezone.utc) + (timedelta(seconds=-1) if expired else timedelta(hours=1))
    return jwt.encode({"sub": str(vendedor_id), "exp": exp}, secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# AsyncClient configurado com overrides
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    mock_estoque_ok: EstoqueClient,
    mock_sqs: SQSClient,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client com banco em memória e clientes externos mockados."""
    import app.config as cfg_module
    cfg_module.settings.jwt_secret = TEST_JWT_SECRET  # type: ignore[assignment]

    app.dependency_overrides[get_db] = lambda: _db_gen(db_session)
    app.dependency_overrides[get_estoque_client] = lambda: mock_estoque_ok
    app.dependency_overrides[get_sqs_client] = lambda: mock_sqs

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


async def _db_gen(session: AsyncSession):
    yield session
