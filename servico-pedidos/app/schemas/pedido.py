import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import StatusPedido

# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------

class ItemPedidoCreate(BaseModel):
    item_id: uuid.UUID
    quantidade: int = Field(gt=0, description="Quantidade deve ser maior que zero")
    preco_unitario: Decimal = Field(gt=0, decimal_places=2)


class ItemPedidoResponse(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    quantidade: int
    preco_unitario: Decimal

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Pedido
# ---------------------------------------------------------------------------

class PedidoCreate(BaseModel):
    itens: list[ItemPedidoCreate] = Field(min_length=1, description="Pelo menos um item é obrigatório")

    @field_validator("itens")
    @classmethod
    def itens_sem_duplicatas(cls, v: list[ItemPedidoCreate]) -> list[ItemPedidoCreate]:
        ids = [str(i.item_id) for i in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Itens duplicados no mesmo pedido não são permitidos")
        return v


class PedidoResponse(BaseModel):
    id: uuid.UUID
    vendedor_id: uuid.UUID
    status: StatusPedido
    total: Decimal
    criado_em: datetime
    atualizado_em: datetime
    itens: list[ItemPedidoResponse]

    model_config = {"from_attributes": True}


class PedidoListResponse(BaseModel):
    total: int
    items: list[PedidoResponse]
