"""Publisher SQS para eventos de pedido.

Decisão: fire-and-forget (ADR-005 + docs/arquitetura.md §4.3).
Falha de publicação é logada mas NÃO bloqueia o retorno 201.

Fila: `caixanamao-pedidos-criados` — **Standard** (at-least-once, sem ordenação).
Por isso:

* nenhum `MessageGroupId`/`MessageDeduplicationId` é enviado: esses parâmetros
  são exclusivos de filas FIFO e o SQS devolve `InvalidParameterValue` quando
  vêm para uma fila Standard (o evento nunca era publicado);
* o consumidor DEVE ser idempotente via `event_id`.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)


class SQSClient:
    """Wrapper sobre boto3 para publicar eventos na fila SQS."""

    def __init__(self) -> None:
        kwargs: dict = {
            "region_name": settings.aws_default_region,
            "aws_access_key_id": settings.aws_access_key_id,
            "aws_secret_access_key": settings.aws_secret_access_key,
        }
        if settings.aws_endpoint_url:
            kwargs["endpoint_url"] = settings.aws_endpoint_url

        self._sqs = boto3.client("sqs", **kwargs)
        self._queue_url: str | None = None

    def _get_queue_url(self) -> str:
        """URL da fila, resolvida uma única vez por processo."""
        if self._queue_url is None:
            response = self._sqs.get_queue_url(QueueName=settings.sqs_queue_pedidos)
            self._queue_url = response["QueueUrl"]
        return self._queue_url

    def publish_pedido_criado(
        self,
        pedido_id: uuid.UUID,
        vendedor_id: uuid.UUID,
        total: str,
        itens: list[dict],
    ) -> bool:
        """Publica o evento PedidoCriado.

        Best-effort: falha é logada e devolvida como `False` — nunca propaga para
        o cliente (a política está em ADR-005 e na matriz de riscos). Chamar
        **após** o commit do pedido.
        """
        event = {
            "event_id": str(uuid.uuid4()),  # idempotência no consumidor
            "event_type": "PedidoCriado",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "pedido_id": str(pedido_id),
                "vendedor_id": str(vendedor_id),
                "total": str(total),
                "itens": itens,
            },
        }
        try:
            self._sqs.send_message(
                QueueUrl=self._get_queue_url(),
                MessageBody=json.dumps(event),
            )
            logger.info("Evento PedidoCriado publicado: pedido_id=%s", pedido_id)
            return True
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "Falha ao publicar PedidoCriado no SQS (pedido_id=%s, fila=%s): %s",
                pedido_id,
                settings.sqs_queue_pedidos,
                exc,
            )
            return False


# Instância singleton
_sqs_client: SQSClient | None = None


def get_sqs_client() -> SQSClient:
    global _sqs_client
    if _sqs_client is None:
        _sqs_client = SQSClient()
    return _sqs_client
