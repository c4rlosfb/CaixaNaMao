"""Testes do health check HTTP (porta 8002)."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.health import criar_app_health


class RedisMorto:
    async def ping(self):
        raise ConnectionError("Connection refused")


class RedisMortoComDetalheSensivel:
    """Erro que carrega host/credencial — não pode aparecer no corpo da resposta."""

    async def ping(self):
        raise ConnectionError("host=db-interno-secreto:6379 senha=abc123 user=estoque_user")


async def _cliente(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_health_liveness(session_factory, redis_fake):
    app = criar_app_health(session_factory=session_factory, redis_client=redis_fake)

    async with await _cliente(app) as cliente:
        resposta = await cliente.get("/health")

    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "servico": "servico-estoque"}


async def test_ready_quando_dependencias_respondem(session_factory, redis_fake):
    app = criar_app_health(session_factory=session_factory, redis_client=redis_fake)

    async with await _cliente(app) as cliente:
        resposta = await cliente.get("/health/ready")

    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ok"


async def test_ready_retorna_503_quando_redis_cai(session_factory):
    app = criar_app_health(session_factory=session_factory, redis_client=RedisMorto())

    async with await _cliente(app) as cliente:
        resposta = await cliente.get("/health/ready")

    assert resposta.status_code == 503
    corpo = resposta.json()
    assert corpo["status"] == "indisponivel"
    assert "redis" in corpo["detalhes"]


async def test_ready_nao_vaza_detalhe_interno_da_excecao(session_factory):
    """O corpo reporta só o status da dependência; o detalhe fica no log."""
    app = criar_app_health(session_factory=session_factory, redis_client=RedisMortoComDetalheSensivel())

    async with await _cliente(app) as cliente:
        resposta = await cliente.get("/health/ready")

    assert resposta.status_code == 503
    assert resposta.json()["detalhes"]["redis"] == "indisponivel"
    for vazamento in ("db-interno-secreto", "abc123", "estoque_user", "ConnectionError"):
        assert vazamento not in resposta.text
