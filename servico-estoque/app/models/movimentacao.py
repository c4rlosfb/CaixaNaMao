"""Modelo da tabela `movimentacoes` — ledger de reservas e liberações."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.models.base import Base


class Movimentacao(Base):
    """Registro imutável de uma movimentação de estoque.

    A restrição única `(pedido_id, item_id, tipo)` é o backstop de idempotência:
    um pedido tem vários itens (o `servico-pedidos` chama `CheckAndReserve` uma
    vez por item, sempre com o mesmo `pedido_id`), mas o mesmo item não pode ser
    reservado nem liberado duas vezes pelo mesmo pedido — um retry após timeout,
    por exemplo, falha na inserção e a transação é revertida, sem débito duplo.
    """

    __tablename__ = "movimentacoes"
    __table_args__ = (
        UniqueConstraint(
            "pedido_id", "item_id", "tipo", name="uq_movimentacoes_pedido_item_tipo"
        ),
        Index("ix_movimentacoes_item_id", "item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("itens.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    quantidade: Mapped[int] = mapped_column(Integer, nullable=False)
    pedido_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return f"Movimentacao(tipo={self.tipo}, pedido_id={self.pedido_id!s}, quantidade={self.quantidade})"
