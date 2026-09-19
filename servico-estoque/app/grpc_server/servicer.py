"""Implementação dos RPCs de `EstoqueService` (shared-protos/estoque.proto).

O servicer é fino de propósito: valida entrada, delega para `EstoqueService` e
traduz o resultado de domínio para o contrato gRPC.

Mapeamento de erros (decidido e documentado no PR):

| Situação de domínio             | Resposta gRPC                                   |
|---------------------------------|-------------------------------------------------|
| Reserva confirmada              | `ReservaResponse(sucesso=True, CONFIRMADO)`     |
| Saldo insuficiente              | `ReservaResponse(sucesso=False, ESTOQUE_INSUFICIENTE)` |
| Lock ocupado / Redis fora do ar | `ReservaResponse(sucesso=False, ESTOQUE_BLOQUEADO)` |
| Item inexistente                | `NOT_FOUND` (o contrato não tem status p/ isso) |
| Retry com parâmetros divergentes| `FAILED_PRECONDITION`                           |
| Entrada inválida                | `INVALID_ARGUMENT`                              |
"""

from __future__ import annotations

import logging
import uuid

import grpc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.event_publisher import EventPublisher
from app.grpc_server.stubs import carregar_stubs
from app.locking.redis_lock import RedisLockManager
from app.services.estoque_service import (
    EstoqueService,
    ResultadoLiberacao,
    ResultadoReserva,
    StatusReserva,
)

logger = logging.getLogger(__name__)


async def _uuid_valido(valor: str, campo: str, context: grpc.aio.ServicerContext) -> uuid.UUID:
    """Converte para UUID ou aborta o RPC com INVALID_ARGUMENT."""
    try:
        return uuid.UUID(str(valor))
    except (ValueError, AttributeError, TypeError):
        await context.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{campo} deve ser um UUID válido (recebido: {valor!r})",
        )
        raise  # pragma: no cover - abort() sempre levanta


def criar_servicer(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lock_manager: RedisLockManager,
    publisher: EventPublisher | None = None,
) -> object:
    """Cria o servicer do `EstoqueService`.

    A fábrica (em vez de uma classe de módulo) permite importar este módulo sem
    os stubs gerados — eles só são exigidos no momento da criação.
    """
    estoque_pb2, estoque_pb2_grpc = carregar_stubs()

    class EstoqueServiceServicer(estoque_pb2_grpc.EstoqueServiceServicer):  # type: ignore[misc]
        """Handler dos 3 RPCs do contrato."""

        async def CheckAndReserve(self, request, context):
            item_id = await _uuid_valido(request.item_id, "item_id", context)
            pedido_id = await _uuid_valido(request.pedido_id, "pedido_id", context)
            request_uuid = await _uuid_valido(
                request.request_uuid or str(uuid.uuid4()), "request_uuid", context
            )
            if request.quantidade <= 0:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "quantidade deve ser maior que zero"
                )

            async with session_factory() as session:
                servico = EstoqueService(session, lock_manager, publisher)
                resultado: ResultadoReserva = await servico.reservar(
                    item_id=item_id,
                    quantidade=request.quantidade,
                    pedido_id=pedido_id,
                    request_uuid=request_uuid,
                )

            if resultado.status is StatusReserva.ITEM_NAO_ENCONTRADO:
                await context.abort(grpc.StatusCode.NOT_FOUND, resultado.mensagem)
            if resultado.status is StatusReserva.CONFLITO_IDEMPOTENCIA:
                await context.abort(grpc.StatusCode.FAILED_PRECONDITION, resultado.mensagem)

            return estoque_pb2.ReservaResponse(
                sucesso=resultado.sucesso,
                status=resultado.status.value,
                mensagem=resultado.mensagem,
            )

        async def ReleaseReserva(self, request, context):
            item_id = await _uuid_valido(request.item_id, "item_id", context)
            pedido_id = await _uuid_valido(request.pedido_id, "pedido_id", context)
            if request.quantidade <= 0:
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT, "quantidade deve ser maior que zero"
                )

            async with session_factory() as session:
                servico = EstoqueService(session, lock_manager, publisher)
                resultado: ResultadoLiberacao = await servico.liberar(
                    item_id=item_id,
                    quantidade=request.quantidade,
                    pedido_id=pedido_id,
                    request_uuid=uuid.uuid4(),
                )

            return estoque_pb2.ReleaseResponse(
                sucesso=resultado.sucesso, mensagem=resultado.mensagem
            )

        async def ConsultarItem(self, request, context):
            item_id = await _uuid_valido(request.item_id, "item_id", context)

            async with session_factory() as session:
                servico = EstoqueService(session, lock_manager, publisher)
                resumo = await servico.consultar(item_id)

            if resumo is None:
                await context.abort(
                    grpc.StatusCode.NOT_FOUND, f"Item {item_id} não encontrado"
                )

            return estoque_pb2.ItemResponse(
                item_id=str(resumo.id),
                nome=resumo.nome,
                quantidade_disponivel=resumo.quantidade_disponivel,
                quantidade_reservada=resumo.quantidade_reservada,
            )

    return EstoqueServiceServicer()
