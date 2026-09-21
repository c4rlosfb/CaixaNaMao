"""Montagem do servidor gRPC (grpc.aio) do servico-estoque."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import grpc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.event_publisher import EventPublisher
from app.grpc_server.servicer import criar_servicer
from app.grpc_server.stubs import carregar_stubs
from app.locking.redis_lock import RedisLockManager

logger = logging.getLogger(__name__)

OPCOES_PADRAO = [
    ("grpc.max_send_message_length", 4 * 1024 * 1024),
    ("grpc.max_receive_message_length", 4 * 1024 * 1024),
    ("grpc.keepalive_time_ms", 30_000),
]


@dataclass(frozen=True)
class ServidorEstoque:
    """Servidor gRPC montado e a porta efetivamente ligada (útil com porta 0)."""

    servidor: grpc.aio.Server
    porta: int


def montar_servidor_grpc(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lock_manager: RedisLockManager,
    publisher: EventPublisher | None = None,
    host: str = "0.0.0.0",
    port: int = 50051,
    max_concurrent_rpcs: int = 100,
) -> ServidorEstoque:
    """Cria (sem iniciar) o servidor gRPC com o `EstoqueService` registrado.

    Não há `ThreadPoolExecutor`: todos os handlers do servicer são `async` e rodam
    no event loop, então `maximum_concurrent_rpcs` é o limite de backpressure que
    o `grpc.aio` de fato aplica (RPCs acima do limite recebem
    `RESOURCE_EXHAUSTED`). A concorrência de banco é limitada pelo pool do
    SQLAlchemy, não por um pool de threads do gRPC.
    """
    _, estoque_pb2_grpc = carregar_stubs()

    servidor = grpc.aio.server(
        maximum_concurrent_rpcs=max_concurrent_rpcs,
        options=OPCOES_PADRAO,
    )
    servicer = criar_servicer(
        session_factory=session_factory, lock_manager=lock_manager, publisher=publisher
    )
    estoque_pb2_grpc.add_EstoqueServiceServicer_to_server(servicer, servidor)

    porta_efetiva = servidor.add_insecure_port(f"{host}:{port}")
    logger.info("Servidor gRPC configurado em %s:%s", host, porta_efetiva)
    return ServidorEstoque(servidor=servidor, porta=porta_efetiva)
