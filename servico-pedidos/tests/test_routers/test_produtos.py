"""Testes dos endpoints de catálogo de produtos e entrega de arquivos estáticos."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.clients.estoque_client import get_estoque_client
from app.main import app

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


async def test_get_produto_nao_encontrado(client: AsyncClient) -> None:
    """Verifica erro 404 para ID inexistente no catálogo."""
    id_inexistente = uuid.uuid4()
    response = await client.get(f"/produtos/{id_inexistente}")

    assert response.status_code == 404
    assert "não encontrado" in response.json()["detail"].lower()


async def test_listar_produtos_com_estoque_indisponivel(client: AsyncClient) -> None:
    """Verifica que quando o gRPC falha/retorna None, os saldos padrão são 0 e o endpoint não quebra."""
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
    finally:
        app.dependency_overrides.pop(get_estoque_client, None)


# ---------------------------------------------------------------------------
# GET / (Entrega de arquivo HTML estático)
# ---------------------------------------------------------------------------

async def test_get_root_entrega_pagina_html(client: AsyncClient) -> None:
    """Verifica se a rota raiz GET / entrega a página HTML com sucesso."""
    response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "CaixaNaMão" in response.text


# ---------------------------------------------------------------------------
# Preservação de rotas existentes
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
    schemas = openapi_resp.json().get("components", {}).get("schemas", {})
    assert "ProdutoResponse" in schemas


# ---------------------------------------------------------------------------
# GET /produtos/dev-tokens
# ---------------------------------------------------------------------------

async def test_get_dev_tokens(client: AsyncClient) -> None:
    """Verifica se os tokens JWT pré-configurados são entregues corretamente."""
    response = await client.get("/produtos/dev-tokens")
    assert response.status_code == 200
    data = response.json()
    assert "vendedor_a" in data
    assert "vendedor_b" in data
    assert "vendedor_a_id" in data
    assert "vendedor_b_id" in data

