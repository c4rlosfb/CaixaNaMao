"""Repositório de pedidos — queries SQLAlchemy isoladas da lógica de negócio."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.pedido import ItemPedido, Pedido
from app.models.enums import StatusPedido
from app.schemas.pedido import ItemPedidoCreate


class PedidoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        vendedor_id: uuid.UUID,
        itens: list[ItemPedidoCreate],
        total: float,
    ) -> Pedido:
        pedido = Pedido(
            vendedor_id=vendedor_id,
            status=StatusPedido.CONFIRMADO,
            total=total,
        )
        pedido.itens = [
            ItemPedido(
                item_id=item.item_id,
                quantidade=item.quantidade,
                preco_unitario=item.preco_unitario,
            )
            for item in itens
        ]
        self._db.add(pedido)
        await self._db.flush()  # obtém o ID sem commitar (commit feito pela dependência)
        await self._db.refresh(pedido, ["itens"])
        return pedido

    async def get_by_id(self, pedido_id: uuid.UUID) -> Pedido | None:
        result = await self._db.execute(
            select(Pedido)
            .options(selectinload(Pedido.itens))
            .where(Pedido.id == pedido_id)
        )
        return result.scalar_one_or_none()

    async def list_by_vendedor(
        self,
        vendedor_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[Pedido]]:
        base_query = select(Pedido).where(Pedido.vendedor_id == vendedor_id)

        count_result = await self._db.execute(
            select(func.count()).select_from(base_query.subquery())
        )
        total = count_result.scalar_one()

        pedidos_result = await self._db.execute(
            base_query
            .options(selectinload(Pedido.itens))
            .order_by(Pedido.criado_em.desc())
            .limit(limit)
            .offset(offset)
        )
        return total, list(pedidos_result.scalars().all())

    async def update_status(self, pedido: Pedido, status: StatusPedido) -> Pedido:
        pedido.status = status
        await self._db.flush()
        return pedido
