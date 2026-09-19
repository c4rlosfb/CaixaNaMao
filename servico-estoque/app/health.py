"""Health check HTTP do servico-estoque (porta 8002 — docs/arquitetura.md §3.3).

`/health`       → liveness (o processo está de pé)
`/health/ready` → readiness (PostgreSQL e Redis respondem)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from sqlalchemy import text
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
        except Exception as exc:  # noqa: BLE001 - reportado no corpo da resposta
            logger.warning("Readiness: PostgreSQL indisponível (%s)", exc)
            problemas["database"] = str(exc)

        try:
            await redis_client.ping()
        except Exception as exc:  # noqa: BLE001 - reportado no corpo da resposta
            logger.warning("Readiness: Redis indisponível (%s)", exc)
            problemas["redis"] = str(exc)

        if problemas:
            response.status_code = 503
            return {"status": "indisponivel", "servico": "servico-estoque", "detalhes": problemas}

        return {"status": "ok", "servico": "servico-estoque"}

    return app
