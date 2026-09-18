"""Serviço de domínio para pedidos.

Orquestra: validação → gRPC (reserva de estoque) → persistência → SQS.
Toda a lógica de negócio fica aqui; routers e repositórios são sem estado.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.estoque_client import EstoqueClient, StatusReserva
from app.clients.sqs_client import SQSClient
from app.models.enums import StatusPedido
from app.models.pedido import Pedido
from app.repositories.pedido_repository import PedidoRepository
from app.schemas.pedido import PedidoCreate

logger = logging.getLogger(__name__)


class PedidoService:
    def __init__(
        self,
        db: AsyncSession,
        estoque: EstoqueClient,
        sqs: SQSClient,
    ) -> None:
        self._repo = PedidoRepository(db)
        self._estoque = estoque
        self._sqs = sqs

    async def criar_pedido(
        self,
        vendedor_id: uuid.UUID,
        payload: PedidoCreate,
    ) -> Pedido:
        """Fluxo completo de criação de pedido (happy path — arquitetura §3.2).

        1. Valida itens (feito nos schemas Pydantic)
        2. Reserva estoque via gRPC para cada item
        3. Persiste o pedido como CONFIRMADO
        4. Publica evento PedidoCriado no SQS (fire-and-forget)
        """
        pedido_id = uuid.uuid4()
        total = sum(
            Decimal(str(item.preco_unitario)) * item.quantidade
            for item in payload.itens
        )

        # Reserva todos os itens — se qualquer um falhar, lança exceção
        reservas_feitas: list[tuple[uuid.UUID, int, uuid.UUID]] = []
        try:
            for item in payload.itens:
                request_uuid = uuid.uuid4()
                result = await self._estoque.check_and_reserve(
                    item_id=item.item_id,
                    quantidade=item.quantidade,
                    pedido_id=pedido_id,
                    request_uuid=request_uuid,
                )

                if not result.sucesso:
                    # Compensação: libera as reservas já feitas neste pedido
                    await self._release_all(reservas_feitas)
                    _raise_for_reserva_status(result.status, item.item_id)

                reservas_feitas.append((item.item_id, item.quantidade, pedido_id))

        except HTTPException:
            raise
        except Exception as exc:
            await self._release_all(reservas_feitas)
            logger.error("Erro inesperado ao reservar estoque: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Serviço de estoque temporariamente indisponível",
            ) from exc

        # Persiste pedido
        pedido = await self._repo.create(
            vendedor_id=vendedor_id,
            itens=payload.itens,
            total=float(total),
        )

        # Publica evento SQS (fire-and-forget — falha não impede o 201)
        self._sqs.publish_pedido_criado(
            pedido_id=pedido.id,
            vendedor_id=vendedor_id,
            total=float(total),
            itens=[
                {
                    "item_id": str(i.item_id),
                    "quantidade": i.quantidade,
                    "preco_unitario": str(i.preco_unitario),
                }
                for i in payload.itens
            ],
        )

        logger.info("Pedido criado com sucesso: id=%s vendedor=%s", pedido.id, vendedor_id)
        return pedido

    async def cancelar_pedido(
        self,
        pedido_id: uuid.UUID,
        vendedor_id: uuid.UUID,
    ) -> Pedido:
        """Cancela um pedido e libera o estoque reservado via gRPC."""
        pedido = await self._repo.get_by_id(pedido_id)

        if pedido is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado")

        if pedido.vendedor_id != vendedor_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão")

        if pedido.status == StatusPedido.CANCELADO:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pedido já cancelado")

        # Libera estoque para cada item
        for item in pedido.itens:
            result = await self._estoque.release_reserva(
                item_id=item.item_id,
                quantidade=item.quantidade,
                pedido_id=pedido_id,
            )
            if not result.sucesso:
                logger.warning(
                    "Falha ao liberar reserva (item=%s pedido=%s): %s",
                    item.item_id,
                    pedido_id,
                    result.mensagem,
                )
                # Não bloqueia o cancelamento — registra e segue (débito: compensação futura)

        return await self._repo.update_status(pedido, StatusPedido.CANCELADO)

    async def _release_all(
        self, reservas: list[tuple[uuid.UUID, int, uuid.UUID]]
    ) -> None:
        """Compensação: libera todas as reservas já feitas para um pedido que falhou."""
        for item_id, quantidade, pedido_id in reservas:
            await self._estoque.release_reserva(item_id, quantidade, pedido_id)


def _raise_for_reserva_status(reserva_status: StatusReserva, item_id: uuid.UUID) -> None:
    """Converte status de reserva gRPC em HTTPException apropriada."""
    if reserva_status == StatusReserva.ESTOQUE_INSUFICIENTE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Estoque insuficiente para o item {item_id}",
        )
    elif reserva_status == StatusReserva.ESTOQUE_BLOQUEADO:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Item {item_id} momentaneamente bloqueado, tente novamente",
            headers={"Retry-After": "1"},
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Erro ao comunicar com o serviço de estoque",
        )
