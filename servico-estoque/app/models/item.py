"""Modelo da tabela `itens` (estoque_db — ver docs/arquitetura.md §3.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.models.base import Base


class Item(Base):
    """Item de catálogo com saldo disponível e reservado.

    Invariantes mantidas pelas operações atômicas do repositório:
      * `quantidade_disponivel >= 0`
      * `quantidade_reservada  >= 0`
      * reserva: disponível -= q, reservada += q
      * liberação: disponível += q, reservada -= q
    """

    __tablename__ = "itens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    preco: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    quantidade_disponivel: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantidade_reservada: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    atualizado_em: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - depuração
        return (
            f"Item(id={self.id!s}, disponivel={self.quantidade_disponivel}, "
            f"reservada={self.quantidade_reservada})"
        )
