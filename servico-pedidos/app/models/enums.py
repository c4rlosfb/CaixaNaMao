import enum


class StatusPedido(str, enum.Enum):
    CONFIRMADO = "CONFIRMADO"
    CANCELADO = "CANCELADO"
    PENDENTE = "PENDENTE"
