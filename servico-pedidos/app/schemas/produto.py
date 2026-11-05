"""Schemas Pydantic e catálogo base de produtos para o servico-pedidos.

Define o modelo de resposta de produtos com seus respectivos saldos de estoque
obtidos via gRPC, além do catálogo padrão inicial (seed_dev.sql).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class ProdutoResponse(BaseModel):
    id: uuid.UUID
    nome: str
    descricao: str
    preco: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Preço unitário com 2 casas decimais (NUMERIC(12,2))",
    )
    quantidade_disponivel: int = Field(
        ge=0,
        description="Saldo disponível em estoque",
    )
    quantidade_reservada: int = Field(
        ge=0,
        description="Quantidade reservada em estoque",
    )
    estoque_disponivel: bool = Field(
        default=True,
        description="False quando o servico-estoque está inacessível ou o item não existe no banco",
    )

    model_config = {"from_attributes": True}


# Catálogo estático dos 4 produtos base definidos em scripts/seed_dev.sql
CATALOGO_PRODUTOS: list[dict] = [
    {
        "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "nome": "Coxinha",
        "descricao": "Salgado de frango assado",
        "preco": Decimal("5.50"),
    },
    {
        "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "nome": "Pastel de carne",
        "descricao": "Pastel frito na hora",
        "preco": Decimal("7.00"),
    },
    {
        "id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
        "nome": "Refrigerante lata",
        "descricao": "Lata 350ml gelada",
        "preco": Decimal("6.00"),
    },
    {
        "id": uuid.UUID("44444444-4444-4444-4444-444444444444"),
        "nome": "Água mineral",
        "descricao": "Garrafa 500ml — estoque unitário para o teste de concorrência",
        "preco": Decimal("3.00"),
    },
]

CATALOGO_PRODUTOS_MAP: dict[uuid.UUID, dict] = {
    p["id"]: p for p in CATALOGO_PRODUTOS
}
