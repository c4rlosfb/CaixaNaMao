"""Clientes externos do servico-estoque (SQS)."""

from __future__ import annotations

from app.clients.event_publisher import (
    EventPublisher,
    NullEventPublisher,
    SQSEventPublisher,
    criar_event_publisher,
    novo_evento_estoque_atualizado,
)

__all__ = [
    "EventPublisher",
    "NullEventPublisher",
    "SQSEventPublisher",
    "criar_event_publisher",
    "novo_evento_estoque_atualizado",
]
