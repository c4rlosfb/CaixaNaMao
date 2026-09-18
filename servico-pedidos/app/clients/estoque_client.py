"""Cliente gRPC para o servico-estoque.

Encapsula o canal gRPC e expõe métodos Python tipados.
O canal é criado uma única vez (singleton) e reutilizado entre requests.

Nota: os stubs (estoque_pb2, estoque_pb2_grpc) são gerados em build time via:
    python -m grpc_tools.protoc \\
        -I../shared-protos \\
        --python_out=. \\
        --grpc_python_out=. \\
        ../shared-protos/estoque.proto

Os arquivos gerados (*_pb2.py) estão no .gitignore — nunca versionados.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum

import grpc

from app.config import settings

logger = logging.getLogger(__name__)


class StatusReserva(str, Enum):
    CONFIRMADO = "CONFIRMADO"
    ESTOQUE_INSUFICIENTE = "ESTOQUE_INSUFICIENTE"
    ESTOQUE_BLOQUEADO = "ESTOQUE_BLOQUEADO"
    ERRO = "ERRO"


@dataclass
class ReservaResult:
    sucesso: bool
    status: StatusReserva
    mensagem: str


@dataclass
class ReleaseResult:
    sucesso: bool
    mensagem: str


class EstoqueClient:
    """Wrapper sobre o stub gRPC do EstoqueService."""

    def __init__(self) -> None:
        target = f"{settings.estoque_grpc_host}:{settings.estoque_grpc_port}"
        self._channel = grpc.aio.insecure_channel(target)
        # Import tardio: stubs só existem após geração via protoc
        try:
            from app.clients import estoque_pb2_grpc  # type: ignore[import]
            self._stub = estoque_pb2_grpc.EstoqueServiceStub(self._channel)
            self._available = True
        except ImportError:
            logger.warning(
                "Stubs gRPC não encontrados. Execute 'make proto' antes de usar "
                "o EstoqueClient em produção. Em testes, use MockEstoqueClient."
            )
            self._stub = None
            self._available = False

    async def check_and_reserve(
        self,
        item_id: uuid.UUID,
        quantidade: int,
        pedido_id: uuid.UUID,
        request_uuid: uuid.UUID,
    ) -> ReservaResult:
        if not self._available or self._stub is None:
            return ReservaResult(
                sucesso=False,
                status=StatusReserva.ESTOQUE_BLOQUEADO,
                mensagem="Serviço de estoque indisponível (stubs não gerados)",
            )
        try:
            from app.clients import estoque_pb2  # type: ignore[import]

            request = estoque_pb2.ReservaRequest(
                item_id=str(item_id),
                quantidade=quantidade,
                pedido_id=str(pedido_id),
                request_uuid=str(request_uuid),
            )
            response = await self._stub.CheckAndReserve(request, timeout=5.0)
            return ReservaResult(
                sucesso=response.sucesso,
                status=StatusReserva(response.status),
                mensagem=response.mensagem,
            )
        except grpc.aio.AioRpcError as exc:
            logger.error("gRPC CheckAndReserve falhou: %s", exc)
            return ReservaResult(
                sucesso=False,
                status=StatusReserva.ERRO,
                mensagem=str(exc),
            )

    async def release_reserva(
        self,
        item_id: uuid.UUID,
        quantidade: int,
        pedido_id: uuid.UUID,
    ) -> ReleaseResult:
        if not self._available or self._stub is None:
            return ReleaseResult(sucesso=False, mensagem="Serviço de estoque indisponível")
        try:
            from app.clients import estoque_pb2  # type: ignore[import]

            request = estoque_pb2.ReleaseRequest(
                item_id=str(item_id),
                quantidade=quantidade,
                pedido_id=str(pedido_id),
            )
            response = await self._stub.ReleaseReserva(request, timeout=5.0)
            return ReleaseResult(sucesso=response.sucesso, mensagem=response.mensagem)
        except grpc.aio.AioRpcError as exc:
            logger.error("gRPC ReleaseReserva falhou: %s", exc)
            return ReleaseResult(sucesso=False, mensagem=str(exc))

    async def close(self) -> None:
        await self._channel.close()


# Instância singleton — inicializada no lifespan da aplicação
_estoque_client: EstoqueClient | None = None


def get_estoque_client() -> EstoqueClient:
    global _estoque_client
    if _estoque_client is None:
        _estoque_client = EstoqueClient()
    return _estoque_client
