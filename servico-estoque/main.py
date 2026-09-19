"""Entrypoint do servico-estoque.

Sobe, no mesmo processo e no mesmo event loop:

* o servidor **gRPC** (porta 50051) com o `EstoqueService` — chamadas internas
  dos outros microsserviços (ADR-001);
* o **health check HTTP** (porta 8002) exigido na §3.3 da arquitetura.

Encerramento gracioso: SIGTERM/SIGINT drenam o gRPC (`grace=5s`), param o
uvicorn e fecham o pool do Redis.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn
from redis.asyncio import Redis

from app.clients.event_publisher import criar_event_publisher
from app.config import alvo_banco_sanitizado, settings
from app.db import SessionFactory
from app.grpc_server.server import montar_servidor_grpc
from app.health import criar_app_health
from app.locking.redis_lock import RedisLockManager

logger = logging.getLogger(__name__)


class _ServidorHealth(uvicorn.Server):
    """uvicorn sem instalador de sinais — o ciclo de vida é do `_executar`."""

    def install_signal_handlers(self) -> None:  # pragma: no cover - trivial
        return


def _configurar_logging() -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(levelname)s | %(name)s | %(message)s",
    )


async def _executar() -> None:
    _configurar_logging()
    logger.info(
        "Iniciando servico-estoque — gRPC :%s | health :%s | db=%s",
        settings.grpc_port,
        settings.health_port,
        alvo_banco_sanitizado(settings.database_url),
    )

    redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    lock_manager = RedisLockManager(redis, ttl_seconds=settings.lock_ttl_seconds)
    publisher = criar_event_publisher(
        queue_name=settings.sqs_queue_estoque,
        endpoint_url=settings.aws_endpoint_url,
        region_name=settings.aws_default_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )

    estoque_grpc = montar_servidor_grpc(
        session_factory=SessionFactory,
        lock_manager=lock_manager,
        publisher=publisher,
        host=settings.grpc_host,
        port=settings.grpc_port,
        max_workers=settings.grpc_max_workers,
    )
    await estoque_grpc.servidor.start()
    logger.info("gRPC escutando em %s:%s", settings.grpc_host, estoque_grpc.porta)

    app_health = criar_app_health(session_factory=SessionFactory, redis_client=redis)
    servidor_health = _ServidorHealth(
        uvicorn.Config(
            app_health,
            host=settings.health_host,
            port=settings.health_port,
            log_level=settings.log_level.lower(),
            access_log=False,
        )
    )

    parar = asyncio.Event()
    loop = asyncio.get_running_loop()
    # Windows não suporta add_signal_handler para SIGTERM — ignorado em dev.
    with contextlib.suppress(NotImplementedError):
        for sinal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sinal, parar.set)

    tarefa_health = asyncio.create_task(servidor_health.serve(), name="health-http")
    tarefa_parada = asyncio.create_task(parar.wait(), name="aguarda-sinal")
    await asyncio.wait({tarefa_health, tarefa_parada}, return_when=asyncio.FIRST_COMPLETED)

    logger.info("Encerrando servico-estoque...")
    servidor_health.should_exit = True
    await asyncio.gather(tarefa_health, return_exceptions=True)
    tarefa_parada.cancel()
    await estoque_grpc.servidor.stop(grace=5)
    await redis.aclose()
    logger.info("servico-estoque encerrado")


def main() -> None:
    """Executa o serviço até receber sinal de parada."""
    asyncio.run(_executar())


if __name__ == "__main__":
    main()
