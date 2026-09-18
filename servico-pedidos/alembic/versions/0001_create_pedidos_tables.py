"""Cria tabelas pedidos e itens_pedido.

Revision ID: 0001
Revises:
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pedidos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("vendedor_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="CONFIRMADO"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "criado_em",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "atualizado_em",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_pedidos_vendedor_id", "pedidos", ["vendedor_id"])

    op.create_table(
        "itens_pedido",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pedido_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pedidos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", UUID(as_uuid=True), nullable=False),
        sa.Column("quantidade", sa.Integer, nullable=False),
        sa.Column("preco_unitario", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_itens_pedido_pedido_id", "itens_pedido", ["pedido_id"])


def downgrade() -> None:
    op.drop_table("itens_pedido")
    op.drop_table("pedidos")
