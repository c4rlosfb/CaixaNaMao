"""Publicação de eventos assíncronos (ADR-005 — AWS SQS / LocalStack).

Política **best-effort** (alinhada à matriz de riscos e à recomendação 7 do
review do PR #23): falha de publicação é logada e **não** bloqueia nem reverte a
operação de estoque já commitada. As filas são Standard (at-least-once), por
isso todo evento carrega um `event_id` para que consumidores futuros possam
deduplicar.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EventPublisher(Protocol):
    """Contrato mínimo de publicação de eventos de estoque."""

    async def publicar_estoque_atualizado(self, *, evento: dict[str, object]) -> None:
        """Publica o evento `EstoqueAtualizado` (best-effort)."""
        ...


class NullEventPublisher:
    """Publisher inerte — usado em testes e quando a mensageria está desligada."""

    async def publicar_estoque_atualizado(self, *, evento: dict[str, object]) -> None:
        logger.debug("Evento descartado (NullEventPublisher): %s", evento.get("event_id"))


class SQSEventPublisher:
    """Publica no SQS via boto3 (chamada síncrona executada em thread)."""

    def __init__(
        self,
        *,
        queue_name: str,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        aws_access_key_id: str = "test",
        aws_secret_access_key: str = "test",
    ) -> None:
        self._queue_name = queue_name
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._cliente = None
        self._url_fila: str | None = None  # cache: evita get_queue_url a cada evento

    def _obter_cliente(self):  # pragma: no cover - exige AWS/LocalStack
        if self._cliente is None:
            import boto3

            self._cliente = boto3.client(
                "sqs",
                endpoint_url=self._endpoint_url,
                region_name=self._region_name,
                aws_access_key_id=self._aws_access_key_id,
                aws_secret_access_key=self._aws_secret_access_key,
            )
        return self._cliente

    def _obter_url_fila(self) -> str:  # pragma: no cover - exige AWS
        """URL da fila, resolvida uma única vez (mesmo padrão do servico-pedidos)."""
        if self._url_fila is None:
            self._url_fila = self._obter_cliente().get_queue_url(QueueName=self._queue_name)[
                "QueueUrl"
            ]
        return self._url_fila

    def _enviar_bloqueante(self, evento: dict[str, object]) -> None:  # pragma: no cover - exige AWS
        try:
            self._obter_cliente().send_message(
                QueueUrl=self._obter_url_fila(), MessageBody=json.dumps(evento, default=str)
            )
        except Exception:
            # Cache pode estar desatualizado (fila recriada): descarta e propaga.
            self._url_fila = None
            raise

    async def publicar_estoque_atualizado(self, *, evento: dict[str, object]) -> None:
        try:
            await asyncio.to_thread(self._enviar_bloqueante, evento)
            logger.info("Evento EstoqueAtualizado publicado (event_id=%s)", evento.get("event_id"))
        except Exception as exc:  # noqa: BLE001 - best-effort: nunca propaga
            logger.warning(
                "Falha ao publicar EstoqueAtualizado (event_id=%s) na fila %s: %s",
                evento.get("event_id"),
                self._queue_name,
                exc,
            )


def criar_event_publisher(
    *,
    queue_name: str,
    endpoint_url: str | None,
    region_name: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
) -> EventPublisher:
    """Fábrica do publisher configurado."""
    return SQSEventPublisher(
        queue_name=queue_name,
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
    )


def novo_evento_estoque_atualizado(
    *,
    tipo_movimentacao: str,
    item_id: uuid.UUID,
    pedido_id: uuid.UUID,
    quantidade: int,
    quantidade_disponivel: int,
    quantidade_reservada: int,
) -> dict[str, object]:
    """Monta o corpo do evento `EstoqueAtualizado` (ADR-005)."""
    return {
        "event_id": str(uuid.uuid4()),
        "evento": "EstoqueAtualizado",
        "tipo_movimentacao": tipo_movimentacao,
        "item_id": str(item_id),
        "pedido_id": str(pedido_id),
        "quantidade": quantidade,
        "quantidade_disponivel": quantidade_disponivel,
        "quantidade_reservada": quantidade_reservada,
        "ocorrido_em": datetime.now(timezone.utc).isoformat(),
    }
