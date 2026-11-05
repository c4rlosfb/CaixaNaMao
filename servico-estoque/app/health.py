"""Health check HTTP do servico-estoque (porta 8002 — docs/arquitetura.md §3.3).

`/health`       → liveness (o processo está de pé)
`/health/ready` → readiness (PostgreSQL e Redis respondem)

O corpo de `/health/ready` reporta apenas quais dependências estão fora —
o detalhe da exceção (host, nome de banco, mensagem do driver) fica no log,
para não expor informação operacional a quem alcança o endpoint.
"""

from __future__ import annotations

import contextlib
import logging

from fastapi import FastAPI, Response
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.telemetry import CorrelationMiddleware

logger = logging.getLogger(__name__)


def criar_app_health(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client,
) -> FastAPI:
    """Monta o app FastAPI de health check."""
    app = FastAPI(
        title="CaixaNaMão — Serviço de Estoque (health)",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(CorrelationMiddleware)

    @app.get("/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "servico": "servico-estoque"}

    @app.get("/health/ready", tags=["health"], summary="Readiness probe")
    async def readiness(response: Response) -> dict[str, object]:
        problemas: dict[str, str] = {}

        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            # Detalhe só no log: o corpo da resposta não expõe host/driver/credenciais.
            logger.warning("Readiness: PostgreSQL indisponível (%s)", exc)
            problemas["database"] = "indisponivel"

        try:
            await redis_client.ping()
        except (RedisError, OSError) as exc:
            logger.warning("Readiness: Redis indisponível (%s)", exc)
            problemas["redis"] = "indisponivel"

        if problemas:
            response.status_code = 503
            return {"status": "indisponivel", "servico": "servico-estoque", "detalhes": problemas}

        return {"status": "ok", "servico": "servico-estoque"}

    @app.post("/reestocar", tags=["demo"], summary="Restaura os saldos padrão para demonstração")
    async def reestocar() -> dict[str, str]:
        sql = text("""
            INSERT INTO itens (id, nome, descricao, preco, quantidade_disponivel, quantidade_reservada)
            VALUES
                ('11111111-1111-1111-1111-111111111111', 'Coxinha',
                 'Salgado de frango assado', 5.50, 10, 0),
                ('22222222-2222-2222-2222-222222222222', 'Pastel de carne',
                 'Pastel frito na hora', 7.00, 5, 0),
                ('33333333-3333-3333-3333-333333333333', 'Refrigerante lata',
                 'Lata 350ml gelada', 6.00, 20, 0),
                ('44444444-4444-4444-4444-444444444444', 'Água mineral',
                 'Garrafa 500ml — estoque unitário para o teste de concorrência', 3.00, 1, 0)
            ON CONFLICT (id) DO UPDATE SET
                quantidade_disponivel = EXCLUDED.quantidade_disponivel,
                quantidade_reservada = EXCLUDED.quantidade_reservada,
                preco = EXCLUDED.preco;
        """)
        async with session_factory() as session:
            await session.execute(sql)
            await session.commit()

        # Limpar locks residuais no Redis
        for item_id in [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
            "33333333-3333-3333-3333-333333333333",
            "44444444-4444-4444-4444-444444444444",
        ]:
            with contextlib.suppress(Exception):
                await redis_client.delete(f"lock:estoque:{item_id}")

        return {"status": "ok", "mensagem": "Estoque restaurado com sucesso"}

    return app
