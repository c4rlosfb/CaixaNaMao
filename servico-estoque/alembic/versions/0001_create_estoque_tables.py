"""Cria tabelas itens e movimentacoes (estoque_db — docs/arquitetura.md §3.3).

Revision ID: 0001
Revises:
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "itens",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("preco", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("quantidade_disponivel", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantidade_reservada", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "atualizado_em",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # Invariantes de saldo: nunca negativo (defesa em profundidade —
        # a aplicação já usa UPDATE condicional).
        sa.CheckConstraint("quantidade_disponivel >= 0", name="ck_itens_disponivel_nao_negativo"),
        sa.CheckConstraint("quantidade_reservada >= 0", name="ck_itens_reservada_nao_negativa"),
    )

    op.create_table(
        "movimentacoes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("itens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("quantidade", sa.Integer(), nullable=False),
        sa.Column("pedido_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "criado_em",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # Idempotência por ITEM: um pedido pode ter vários itens (o servico-pedidos
        # chama CheckAndReserve uma vez por item, com o mesmo pedido_id), mas não
        # pode reservar nem liberar o mesmo item duas vezes.
        sa.UniqueConstraint(
            "pedido_id", "item_id", "tipo", name="uq_movimentacoes_pedido_item_tipo"
        ),
    )
    op.create_index("ix_movimentacoes_item_id", "movimentacoes", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_movimentacoes_item_id", table_name="movimentacoes")
    op.drop_table("movimentacoes")
    op.drop_table("itens")
