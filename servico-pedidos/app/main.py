"""Entrypoint da aplicação FastAPI — servico-pedidos."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.clients.estoque_client import EstoqueClient, get_estoque_client
from app.db import dispose_engine
from app.routers import pedidos, produtos
from app.telemetry import CorrelationMiddleware, configurar_logging

configurar_logging()
logger = logging.getLogger(__name__)



@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Inicializa e encerra recursos compartilhados (canal gRPC, pool do banco)."""
    logger.info("Iniciando servico-pedidos...")
    client: EstoqueClient = get_estoque_client()
    yield
    logger.info("Encerrando servico-pedidos...")
    await client.close()
    # Fecha o pool do SQLAlchemy (sem isso as conexões ficam até o processo morrer).
    await dispose_engine()


app = FastAPI(
    title="CaixaNaMão — Serviço de Pedidos",
    description=(
        "Responsável pela criação, consulta e cancelamento de pedidos. "
        "Coordena reserva de estoque via gRPC e publica eventos no SQS."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(CorrelationMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringir em produção
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Correlation-ID",
        "X-Request-ID",
    ],
    expose_headers=["X-Correlation-ID", "X-Request-ID"],
)

app.include_router(pedidos.router)
app.include_router(produtos.router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False, response_model=None)
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return {"message": "CaixaNaMão — Serviço de Pedidos"}



@app.get("/health", tags=["health"], summary="Health check")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "servico-pedidos"}

