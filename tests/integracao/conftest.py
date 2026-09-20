"""Fixtures da suíte de integração Pedidos <-> Estoque (issue #7).

Como rodar
----------
    bash scripts/validar_integracao.sh          # sobe a stack e roda a suíte
    # ou, com a stack já no ar:
    pytest tests/integracao -v

A suíte é **black-box**: fala com os serviços por HTTP/gRPC e com o PostgreSQL do
estoque por SQL, sem importar código dos serviços. É assim que ela valida o
contrato (`shared-protos/estoque.proto`) de fora para dentro, do ponto de vista de
quem consome — que é justamente o que a integração pede.

Sem a stack no ar (ou sem as dependências de teste), os testes são **pulados** com
instruções no motivo: a suíte nunca falha por ambiente ausente, e nunca "passa"
fingindo que verificou algo.

Variáveis de ambiente (todas com default de desenvolvimento):

| Variável | Default | Para que serve |
|---|---|---|
| `PEDIDOS_BASE_URL` | `http://127.0.0.1:8000` | REST do servico-pedidos |
| `ESTOQUE_GRPC_ADDRESS` | `127.0.0.1:50051` | gRPC do servico-estoque |
| `ESTOQUE_DATABASE_URL` | `postgresql://estoque_user:estoque_pass@127.0.0.1:5432/estoque_db` | semear/observar itens no estoque |
| `JWT_SECRET` | `dev-secret-troque-em-producao` | assinar o token do vendedor (HS256, ADR-001) |
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CONTRATO = RAIZ / "shared-protos" / "estoque.proto"

PEDIDOS_BASE_URL = os.environ.get("PEDIDOS_BASE_URL", "http://127.0.0.1:8000")
ESTOQUE_GRPC_ADDRESS = os.environ.get("ESTOQUE_GRPC_ADDRESS", "127.0.0.1:50051")
ESTOQUE_DATABASE_URL = os.environ.get(
    "ESTOQUE_DATABASE_URL",
    "postgresql://estoque_user:estoque_pass@127.0.0.1:5432/estoque_db",
)
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-troque-em-producao")

INSTRUCOES = (
    "stack de integração indisponível. Suba com `docker compose up -d --build` "
    "(ou `bash scripts/validar_integracao.sh`). Se os serviços estiverem em outra "
    "porta/host, ajuste PEDIDOS_BASE_URL, ESTOQUE_GRPC_ADDRESS, "
    "ESTOQUE_DATABASE_URL e JWT_SECRET."
)


# ---------------------------------------------------------------------------
# Acesso ao banco do estoque (só leitura/observação e semeadura de itens)
# ---------------------------------------------------------------------------
def consultar(sql: str, *parametros) -> list:
    """Executa um SELECT/INSERT ... RETURNING no PostgreSQL do estoque."""
    import asyncpg

    async def _executar():
        conexao = await asyncpg.connect(ESTOQUE_DATABASE_URL)
        try:
            return await conexao.fetch(sql, *parametros)
        finally:
            await conexao.close()

    return asyncio.run(_executar())


def executar(sql: str, *parametros) -> None:
    """Executa um comando sem retorno (INSERT/UPDATE/DELETE)."""
    import asyncpg

    async def _executar():
        conexao = await asyncpg.connect(ESTOQUE_DATABASE_URL)
        try:
            await conexao.execute(sql, *parametros)
        finally:
            await conexao.close()

    asyncio.run(_executar())


# ---------------------------------------------------------------------------
# Detecção da stack
# ---------------------------------------------------------------------------
def _tcp_acessivel(host: str, porta: int) -> bool:
    try:
        with socket.create_connection((host, porta), timeout=1.5):
            return True
    except OSError:
        return False


def _http_acessivel(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as resposta:
            return resposta.status == 200
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def stack() -> None:
    """Pula a suíte quando a stack não está no ar, dizendo exatamente o que falta."""
    faltando: list[str] = []

    if not _http_acessivel(f"{PEDIDOS_BASE_URL}/health"):
        faltando.append(f"servico-pedidos (REST) em {PEDIDOS_BASE_URL}/health")

    host_grpc, _, porta_grpc = ESTOQUE_GRPC_ADDRESS.partition(":")
    if not _tcp_acessivel(host_grpc or "127.0.0.1", int(porta_grpc or 50051)):
        faltando.append(f"servico-estoque (gRPC) em {ESTOQUE_GRPC_ADDRESS}")

    try:
        consultar("SELECT 1")
    except Exception as erro:  # noqa: BLE001 - qualquer falha significa ambiente ausente
        faltando.append(f"PostgreSQL do estoque ({type(erro).__name__})")

    if faltando:
        pytest.skip(f"{INSTRUCOES} Não encontrei: {'; '.join(faltando)}")


# ---------------------------------------------------------------------------
# Contrato: stubs gerados em tempo de teste (nunca versionados)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def stubs():
    """Compila o contrato em um diretório temporário e devolve os módulos gerados."""
    try:
        import grpc_tools.protoc  # noqa: F401
    except ImportError:  # pragma: no cover - caminho de ambiente incompleto
        pytest.skip(
            "grpcio-tools não instalado: "
            "pip install -r tests/integracao/requirements.txt"
        )

    destino = Path(tempfile.mkdtemp(prefix="cnm-stubs-"))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{CONTRATO.parent}",
            f"--python_out={destino}",
            f"--grpc_python_out={destino}",
            str(CONTRATO),
        ],
        check=True,
        capture_output=True,
    )
    sys.path.insert(0, str(destino))

    import estoque_pb2  # type: ignore[import-not-found]
    import estoque_pb2_grpc  # type: ignore[import-not-found]

    return estoque_pb2, estoque_pb2_grpc


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------
@pytest.fixture
def pedidos(stack):
    """Cliente HTTP do servico-pedidos."""
    import httpx

    with httpx.Client(base_url=PEDIDOS_BASE_URL, timeout=30.0) as cliente:
        yield cliente


@pytest.fixture
def estoque(stubs, stack):
    """Stub gRPC do servico-estoque (cliente real, gerado do contrato)."""
    import grpc

    _, modulo_grpc = stubs
    canal = grpc.insecure_channel(ESTOQUE_GRPC_ADDRESS)
    try:
        yield modulo_grpc.EstoqueServiceStub(canal)
    finally:
        canal.close()


@pytest.fixture
def autorizacao(vendedor_id: uuid.UUID) -> dict[str, str]:
    """Cabeçalho Authorization com um JWT HS256 válido (ADR-001)."""
    return _cabecalho_de_token(vendedor_id)


@pytest.fixture
def token_de():
    """Fábrica de cabeçalho Authorization para um vendedor arbitrário."""
    return _cabecalho_de_token


def _cabecalho_de_token(vendedor_id: uuid.UUID) -> dict[str, str]:
    import jwt

    token = jwt.encode(
        {
            "sub": str(vendedor_id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def vendedor_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def item_factory():
    """Cria itens no estoque e apaga o que criou ao final do teste.

    O contrato não expõe criação de item (o cadastro não faz parte da issue), então
    a suíte semeia direto na tabela `itens` — e limpa depois, incluindo o ledger.
    """
    criados: list[uuid.UUID] = []

    def _criar(*, quantidade: int, nome: str = "Item de teste") -> uuid.UUID:
        item_id = uuid.uuid4()
        executar(
            "INSERT INTO itens "
            "(id, nome, descricao, preco, quantidade_disponivel, quantidade_reservada) "
            "VALUES ($1, $2, $3, $4, $5, 0)",
            item_id,
            nome,
            "item criado pela suite de integracao",
            Decimal("1.00"),
            quantidade,
        )
        criados.append(item_id)
        return item_id

    yield _criar

    for item_id in criados:
        executar("DELETE FROM movimentacoes WHERE item_id = $1", item_id)
        executar("DELETE FROM itens WHERE id = $1", item_id)


def estado_do_item(item_id: uuid.UUID) -> tuple[int, int]:
    """(quantidade_disponivel, quantidade_reservada) lidos direto do banco."""
    linhas = consultar(
        "SELECT quantidade_disponivel, quantidade_reservada FROM itens WHERE id = $1",
        item_id,
    )
    assert linhas, f"item {item_id} não existe no estoque"
    return (linhas[0]["quantidade_disponivel"], linhas[0]["quantidade_reservada"])


@pytest.fixture
def estado():
    """Fica disponível como fixture para os testes não importarem o conftest."""
    return estado_do_item
