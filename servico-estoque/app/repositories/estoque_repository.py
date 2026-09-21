"""Repositório de estoque — operações atômicas sobre `itens` e `movimentacoes`.

Ponto-chave de correção (ADR-004 + review do PR #23, rec. 11): a garantia de
"nunca vender o último item duas vezes" **não** vem do TTL do lock do Redis, e
sim da condição transacional avaliada pelo PostgreSQL na própria escrita:

    UPDATE itens SET ... WHERE id = :item_id AND quantidade_disponivel >= :q

Se a linha não for afetada, a reserva não aconteceu. Isso vale mesmo que o lock
tenha expirado durante a operação (o Redis é otimização de contenção, o banco é
a fonte de verdade).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TipoMovimentacao
from app.models.item import Item
from app.models.movimentacao import Movimentacao


class EstoqueRepository:
    """Operações de banco usadas pelo serviço de domínio (sem commit implícito)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------
    async def buscar_item(self, item_id: uuid.UUID) -> Item | None:
        return await self._session.get(Item, item_id)

    async def buscar_movimentacao(
        self,
        pedido_id: uuid.UUID,
        tipo: TipoMovimentacao,
        item_id: uuid.UUID,
    ) -> Movimentacao | None:
        """Movimentação de um pedido **para um item específico**.

        A chave de idempotência é `(pedido_id, item_id, tipo)`: um pedido com
        vários itens registra uma reserva por item, e o retry do mesmo item é o
        que precisa ser detectado.
        """
        stmt = select(Movimentacao).where(
            Movimentacao.pedido_id == pedido_id,
            Movimentacao.item_id == item_id,
            Movimentacao.tipo == tipo.value,
        )
        return await self._session.scalar(stmt)

    # ------------------------------------------------------------------
    # Escrita atômica (guarda condicional no próprio UPDATE)
    # ------------------------------------------------------------------
    async def reservar_atomico(self, item_id: uuid.UUID, quantidade: int) -> Item | None:
        """Debita `quantidade` do saldo disponível se (e somente se) houver saldo.

        Retorna o item atualizado, ou `None` quando o item não existe **ou** não
        há saldo suficiente — o chamador distingue os dois casos com uma leitura.
        """
        stmt = (
            update(Item)
            .where(Item.id == item_id, Item.quantidade_disponivel >= quantidade)
            .values(
                quantidade_disponivel=Item.quantidade_disponivel - quantidade,
                quantidade_reservada=Item.quantidade_reservada + quantidade,
                atualizado_em=func.now(),
            )
            .returning(Item)
        )
        return await self._session.scalar(stmt)

    async def liberar_atomico(self, item_id: uuid.UUID, quantidade: int) -> Item | None:
        """Devolve `quantidade` ao saldo disponível se houver reserva correspondente."""
        stmt = (
            update(Item)
            .where(Item.id == item_id, Item.quantidade_reservada >= quantidade)
            .values(
                quantidade_disponivel=Item.quantidade_disponivel + quantidade,
                quantidade_reservada=Item.quantidade_reservada - quantidade,
                atualizado_em=func.now(),
            )
            .returning(Item)
        )
        return await self._session.scalar(stmt)

    async def registrar_movimentacao(
        self,
        item_id: uuid.UUID,
        tipo: TipoMovimentacao,
        quantidade: int,
        pedido_id: uuid.UUID,
    ) -> Movimentacao:
        movimentacao = Movimentacao(
            item_id=item_id,
            tipo=tipo.value,
            quantidade=quantidade,
            pedido_id=pedido_id,
        )
        self._session.add(movimentacao)
        await self._session.flush()
        return movimentacao
