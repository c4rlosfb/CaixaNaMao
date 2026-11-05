"""Testes dos endpoints de catálogo de produtos, cliente gRPC e arquivos estáticos."""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import grpc
import jwt
import pytest
from httpx import AsyncClient

from app.clients.estoque_client import EstoqueClient, get_estoque_client
from app.config import settings
from app.main import STATIC_DIR, app
from app.schemas.produto import CATALOGO_PRODUTOS

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET /produtos (listagem de catálogo)
# ---------------------------------------------------------------------------

async def test_listar_produtos_sucesso(client: AsyncClient) -> None:
    """Verifica se GET /produtos retorna os 4 produtos do seed com saldos."""
    response = await client.get("/produtos")

    assert response.status_code == 200
    produtos = response.json()
    assert len(produtos) == 4

    nomes = [p["nome"] for p in produtos]
    assert "Coxinha" in nomes
    assert "Pastel de carne" in nomes
    assert "Refrigerante lata" in nomes
    assert "Água mineral" in nomes

    # Todos os produtos devem possuir id, nome, descricao, preco e estoque
    for p in produtos:
        assert "id" in p
        assert "nome" in p
        assert "descricao" in p
        assert Decimal(str(p["preco"])) > 0
        assert p["quantidade_disponivel"] >= 0
        assert p["quantidade_reservada"] >= 0
        assert p["estoque_disponivel"] is True


# ---------------------------------------------------------------------------
# GET /produtos/{id} (detalhe de produto)
# ---------------------------------------------------------------------------

async def test_get_produto_sucesso(client: AsyncClient) -> None:
    """Verifica a consulta unitária de produto existente."""
    coxinha_id = "11111111-1111-1111-1111-111111111111"
    response = await client.get(f"/produtos/{coxinha_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == coxinha_id
    assert data["nome"] == "Coxinha"
    assert data["preco"] == "5.50"
    assert data["quantidade_disponivel"] == 10
    assert data["quantidade_reservada"] == 0
    assert data["estoque_disponivel"] is True


async def test_get_produto_nao_encontrado(client: AsyncClient) -> None:
    """Verifica erro 404 para ID inexistente no catálogo."""
    id_inexistente = uuid.uuid4()
    response = await client.get(f"/produtos/{id_inexistente}")

    assert response.status_code == 404
    assert "não encontrado" in response.json()["detail"].lower()


async def test_listar_produtos_com_estoque_indisponivel(client: AsyncClient) -> None:
    """Verifica que quando o gRPC falha/retorna None, os saldos padrão são 0 e estoque_disponivel é False."""
    mock_sem_estoque = AsyncMock()
    mock_sem_estoque.consultar_item = AsyncMock(return_value=None)
    app.dependency_overrides[get_estoque_client] = lambda: mock_sem_estoque

    try:
        response = await client.get("/produtos")
        assert response.status_code == 200
        produtos = response.json()
        assert len(produtos) == 4
        for p in produtos:
            assert p["quantidade_disponivel"] == 0
            assert p["quantidade_reservada"] == 0
            assert p["estoque_disponivel"] is False
    finally:
        app.dependency_overrides.pop(get_estoque_client, None)


# ---------------------------------------------------------------------------
# GET / (Entrega de arquivo HTML estático e fallback JSON)
# ---------------------------------------------------------------------------

async def test_get_root_entrega_pagina_html(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica se a rota raiz GET / entrega a página HTML quando index.html existe."""
    if not (STATIC_DIR / "index.html").exists():
        (tmp_path / "index.html").write_text(
            "<!DOCTYPE html><html><head><title>CaixaNaMão</title></head><body>CaixaNaMão</body></html>",
            encoding="utf-8",
        )
        monkeypatch.setattr("app.main.STATIC_DIR", tmp_path)

    response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "CaixaNaMão" in response.text


async def test_get_root_fallback_json_sem_index_html(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica o fallback JSON da rota raiz quando static/index.html não está presente."""
    monkeypatch.setattr("app.main.STATIC_DIR", tmp_path)

    response = await client.get("/")

    assert response.status_code == 200
    assert "application/json" in response.headers.get("content-type", "")
    assert response.json() == {"message": "CaixaNaMão — Serviço de Pedidos"}


# ---------------------------------------------------------------------------
# Preservação de rotas existentes e sincronia com seed_dev.sql
# ---------------------------------------------------------------------------

async def test_rotas_existentes_preservadas(client: AsyncClient) -> None:
    """Garante que /health, /docs e /openapi.json continuam funcionando."""
    health_resp = await client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"

    docs_resp = await client.get("/docs")
    assert docs_resp.status_code == 200

    openapi_resp = await client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    openapi_data = openapi_resp.json()
    schemas = openapi_data.get("components", {}).get("schemas", {})
    assert "ProdutoResponse" in schemas
    assert "/produtos/dev-tokens" not in openapi_data.get("paths", {})


async def test_catalogo_sincronizado_com_seed_dev() -> None:
    """Garante que os IDs, nomes e preços em CATALOGO_PRODUTOS estão alinhados com scripts/seed_dev.sql."""
    seed_path = Path(__file__).resolve().parents[3] / "scripts" / "seed_dev.sql"
    conteudo_seed = seed_path.read_text(encoding="utf-8")

    for produto in CATALOGO_PRODUTOS:
        assert str(produto["id"]) in conteudo_seed
        assert produto["nome"] in conteudo_seed
        assert f"{produto['preco']:.2f}" in conteudo_seed


# ---------------------------------------------------------------------------
# GET /produtos/dev-tokens (Feature flag de desenvolvimento)
# ---------------------------------------------------------------------------

async def test_get_dev_tokens_desabilitado_por_padrao(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que /produtos/dev-tokens retorna 404 quando permitir_dev_tokens é False (default)."""
    monkeypatch.setattr(settings, "permitir_dev_tokens", False)
    response = await client.get("/produtos/dev-tokens")
    assert response.status_code == 404


async def test_get_dev_tokens_habilitado_emite_jwt_valido(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que quando permitir_dev_tokens=True, os JWTs emitidos têm assinatura e sub válidos."""
    monkeypatch.setattr(settings, "permitir_dev_tokens", True)
    response = await client.get("/produtos/dev-tokens")
    assert response.status_code == 200
    data = response.json()

    assert data["vendedor_a_id"] == "11111111-0000-0000-0000-000000000001"
    assert data["vendedor_b_id"] == "22222222-0000-0000-0000-000000000002"

    payload_a = jwt.decode(
        data["vendedor_a"],
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    payload_b = jwt.decode(
        data["vendedor_b"],
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    assert payload_a["sub"] == data["vendedor_a_id"]
    assert payload_a["nome"] == "Carlos"
    assert payload_b["sub"] == data["vendedor_b_id"]


# ---------------------------------------------------------------------------
# Testes unitários diretos de EstoqueClient.consultar_item
# ---------------------------------------------------------------------------

async def test_estoque_client_consultar_item_cenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valida o mapeamento de ConsultarItem (sucesso, NOT_FOUND, falha RPC e cliente indisponível)."""
    item_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    fake_pb2 = SimpleNamespace(ItemRequest=lambda item_id: SimpleNamespace(item_id=item_id))
    monkeypatch.setitem(sys.modules, "app.clients.estoque_pb2", fake_pb2)

    cliente = EstoqueClient.__new__(EstoqueClient)

    # 1. Cliente indisponível (_available = False)
    cliente._available = False
    cliente._stub = None
    assert await cliente.consultar_item(item_id) is None

    # 2. Sucesso no RPC ConsultarItem
    cliente._available = True
    stub_mock = MagicMock()
    stub_mock.ConsultarItem = AsyncMock(
        return_value=SimpleNamespace(
            item_id=str(item_id),
            nome="Coxinha",
            quantidade_disponivel=10,
            quantidade_reservada=2,
        )
    )
    cliente._stub = stub_mock
    resultado = await cliente.consultar_item(item_id)
    assert resultado is not None
    assert resultado.item_id == item_id
    assert resultado.nome == "Coxinha"
    assert resultado.quantidade_disponivel == 10
    assert resultado.quantidade_reservada == 2

    # 3. NOT_FOUND -> retorna None
    class FakeRpcError(grpc.aio.AioRpcError):
        def __init__(self, code: grpc.StatusCode, details: str = "fake error") -> None:
            super().__init__(
                code=code,
                initial_metadata=(),
                trailing_metadata=(),
                details=details,
                debug_error_string="",
            )

        def code(self) -> grpc.StatusCode:
            return self._code

    stub_mock.ConsultarItem = AsyncMock(side_effect=FakeRpcError(grpc.StatusCode.NOT_FOUND))
    assert await cliente.consultar_item(item_id) is None

    # 4. Erro de transporte / UNAVAILABLE -> retorna None
    stub_mock.ConsultarItem = AsyncMock(side_effect=FakeRpcError(grpc.StatusCode.UNAVAILABLE))
    assert await cliente.consultar_item(item_id) is None


