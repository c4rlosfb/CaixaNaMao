import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import StatusPedido


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Pedido(Base):
    __tablename__ = "pedidos"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    vendedor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[StatusPedido] = mapped_column(
        String(20), nullable=False, default=StatusPedido.CONFIRMADO
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # `DateTime(timezone=True)` é obrigatório para casar com o `TIMESTAMP WITH TIME
    # ZONE` da migration: sem o tipo explícito o SQLAlchemy infere um timestamp
    # naive e o asyncpg recusa o `datetime` com UTC no INSERT (bug pego no e2e
    # contra PostgreSQL real — o SQLite dos testes aceita).
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    itens: Mapped[list["ItemPedido"]] = relationship(
        "ItemPedido", back_populates="pedido", cascade="all, delete-orphan"
    )


class ItemPedido(Base):
    __tablename__ = "itens_pedido"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pedido_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pedidos.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quantidade: Mapped[int] = mapped_column(nullable=False)
    preco_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    pedido: Mapped["Pedido"] = relationship("Pedido", back_populates="itens")
