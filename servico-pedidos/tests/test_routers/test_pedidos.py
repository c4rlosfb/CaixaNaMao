"""Testes de integração dos endpoints REST de pedidos."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, AsyncMock

import pytest
from httpx import AsyncClient

from app.clients.estoque_client import ReservaResult, ReleaseResult, StatusReserva, get_estoque_client
from app.clients.sqs_client import get_sqs_client
from app.dependencies import get_db
from app.main import app
from tests.conftest import TEST_VENDEDOR_ID, make_test_token, _db_gen, _TestSessionFactory

pytestmark = pytest.mark.asyncio


VALID_TOKEN = make_test_token()

PAYLOAD_VALIDO = {
    "itens": [
        {
            "item_id": str(uuid.uuid4()),
            "quantidade": 2,
            "preco_unitario": "15.50",
        }
    ]
}


# ---------------------------------------------------------------------------
# POST /pedidos
# ---------------------------------------------------------------------------

async def test_criar_pedido_sucesso(client: AsyncClient) -> None:
    response = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "CONFIRMADO"
    assert data["vendedor_id"] == str(TEST_VENDEDOR_ID)
    assert len(data["itens"]) == 1
    assert "id" in data


async def test_criar_pedido_sem_autenticacao(client: AsyncClient) -> None:
    response = await client.post("/pedidos", json=PAYLOAD_VALIDO)
    assert response.status_code == 403  # HTTPBearer retorna 403 quando ausente


async def test_criar_pedido_token_invalido(client: AsyncClient) -> None:
    response = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": "Bearer token.invalido.aqui"},
    )
    assert response.status_code == 401


async def test_criar_pedido_token_expirado(client: AsyncClient) -> None:
    token = make_test_token(expired=True)
    response = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


async def test_criar_pedido_estoque_insuficiente(
    db_session, mock_estoque_insuficiente, mock_sqs
) -> None:
    app.dependency_overrides[get_db] = lambda: _db_gen(db_session)
    app.dependency_overrides[get_estoque_client] = lambda: mock_estoque_insuficiente
    app.dependency_overrides[get_sqs_client] = lambda: mock_sqs

    import app.config as cfg_module
    cfg_module.settings.jwt_secret = "secret-de-teste-nao-usar-em-producao"

    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            "/pedidos",
            json=PAYLOAD_VALIDO,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "insuficiente" in response.json()["detail"].lower()


async def test_criar_pedido_sem_itens(client: AsyncClient) -> None:
    response = await client.post(
        "/pedidos",
        json={"itens": []},
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 422  # validação Pydantic


async def test_criar_pedido_quantidade_zero(client: AsyncClient) -> None:
    payload = {
        "itens": [{"item_id": str(uuid.uuid4()), "quantidade": 0, "preco_unitario": "10.00"}]
    }
    response = await client.post(
        "/pedidos",
        json=payload,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /pedidos/{id}
# ---------------------------------------------------------------------------

async def test_get_pedido_sucesso(client: AsyncClient) -> None:
    # Cria o pedido primeiro
    create_resp = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert create_resp.status_code == 201
    pedido_id = create_resp.json()["id"]

    # Consulta
    get_resp = await client.get(
        f"/pedidos/{pedido_id}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == pedido_id


async def test_get_pedido_nao_encontrado(client: AsyncClient) -> None:
    response = await client.get(
        f"/pedidos/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 404


async def test_get_pedido_outro_vendedor(client: AsyncClient) -> None:
    # Cria pedido com vendedor A
    create_resp = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    pedido_id = create_resp.json()["id"]

    # Tenta acessar com vendedor B
    token_b = make_test_token(vendedor_id=uuid.uuid4())
    get_resp = await client.get(
        f"/pedidos/{pedido_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert get_resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /pedidos (listagem)
# ---------------------------------------------------------------------------

async def test_listar_pedidos(client: AsyncClient) -> None:
    # Cria 2 pedidos
    for _ in range(2):
        await client.post(
            "/pedidos",
            json=PAYLOAD_VALIDO,
            headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        )

    response = await client.get(
        "/pedidos",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# PATCH /pedidos/{id}/cancelar
# ---------------------------------------------------------------------------

async def test_cancelar_pedido_sucesso(client: AsyncClient) -> None:
    create_resp = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    pedido_id = create_resp.json()["id"]

    cancel_resp = await client.patch(
        f"/pedidos/{pedido_id}/cancelar",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELADO"


async def test_cancelar_pedido_ja_cancelado(client: AsyncClient) -> None:
    create_resp = await client.post(
        "/pedidos",
        json=PAYLOAD_VALIDO,
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    pedido_id = create_resp.json()["id"]

    await client.patch(
        f"/pedidos/{pedido_id}/cancelar",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    # Segunda tentativa
    resp = await client.patch(
        f"/pedidos/{pedido_id}/cancelar",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
