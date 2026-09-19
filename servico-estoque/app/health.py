"""Health check HTTP do servico-estoque (porta 8002 — docs/arquitetura.md §3.3).

`/health`       → liveness (o processo está de pé)
`/health/ready` → readiness (PostgreSQL e Redis respondem)

O corpo de `/health/ready` reporta apenas quais dependências estão fora —
o detalhe da exceção (host, nome de banco, mensagem do driver) fica no log,
para não expor informação operacional a quem alcança o endpoint.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

    return app
