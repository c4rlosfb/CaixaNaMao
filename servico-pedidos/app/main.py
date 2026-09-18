"""Entrypoint da aplicação FastAPI — servico-pedidos."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.estoque_client import EstoqueClient, get_estoque_client
from app.routers import pedidos

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Inicializa e encerra recursos compartilhados (canal gRPC, etc.)."""
    logger.info("Iniciando servico-pedidos...")
    client: EstoqueClient = get_estoque_client()
    yield
    logger.info("Encerrando servico-pedidos...")
    await client.close()


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restringir em produção
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(pedidos.router)


@app.get("/health", tags=["health"], summary="Health check")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "servico-pedidos"}
