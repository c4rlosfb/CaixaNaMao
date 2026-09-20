"""Fixtures compartilhadas entre todos os testes do servico-pedidos.

Princípio: os testes usam a **API real** do serviço (`PedidoService(db, estoque,
sqs)`, sessão de banco verdadeira) e só os clientes externos (gRPC/SQS) são
substituídos. Assim o contrato testado é o que roda em produção — contratos de
mock divergentes foram um dos bloqueadores do review do PR #26.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.clients.estoque_client import (
    EstoqueClient,
    ReleaseResult,
    ReservaResult,
    StatusReserva,
    get_estoque_client,
)
from app.clients.sqs_client import SQSClient, get_sqs_client
from app.config import settings
from app.dependencies import get_db
from app.main import app
from app.models.base import Base

# ---------------------------------------------------------------------------
# Banco de testes: SQLite async em arquivo (isolado por execução)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sqlite_url(tmp_path_factory) -> str:
    caminho = tmp_path_factory.mktemp("db") / "pedidos_test.db"
    return f"sqlite+aiosqlite:///{caminho.as_posix()}"


@pytest_asyncio.fixture
async def engine(sqlite_url: str):
    eng = create_async_engine(sqlite_url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def setup_db(engine) -> AsyncGenerator[None, None]:
    """Recria as tabelas antes de cada teste (isolamento entre testes)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Mocks dos clientes externos (gRPC e SQS)
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
    """Publisher SQS síncrono (fire-and-forget), como em produção."""
    mock = MagicMock(spec=SQSClient)
    mock.publish_pedido_criado = MagicMock(return_value=True)
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
# AsyncClient com overrides (banco de teste + clientes mockados)
# ---------------------------------------------------------------------------


def _configurar_overrides(
    db_session: AsyncSession,
    estoque: EstoqueClient,
    sqs: SQSClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aponta as dependências do app para o banco de teste e os mocks informados."""
    # monkeypatch em vez de mutar o objeto global: o valor é restaurado no fim do teste.
    monkeypatch.setattr(settings, "jwt_secret", TEST_JWT_SECRET, raising=True)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_estoque_client] = lambda: estoque
    app.dependency_overrides[get_sqs_client] = lambda: sqs


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    mock_estoque_ok: EstoqueClient,
    mock_sqs: SQSClient,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client com banco de teste e estoque respondendo com sucesso."""
    _configurar_overrides(db_session, mock_estoque_ok, mock_sqs, monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_estoque_insuficiente(
    db_session: AsyncSession,
    mock_estoque_insuficiente: EstoqueClient,
    mock_sqs: SQSClient,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client com estoque recusando a reserva por saldo insuficiente."""
    _configurar_overrides(db_session, mock_estoque_insuficiente, mock_sqs, monkeypatch)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
