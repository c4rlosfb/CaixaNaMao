"""Serviço de domínio para pedidos.

Orquestra: validação → gRPC (reserva de estoque) → persistência → SQS.
Toda a lógica de negócio fica aqui; routers e repositórios são sem estado.

Ordem dos efeitos externos (correção do review do PR #26):

1. reserva de estoque via gRPC (por item);
2. persistência do pedido **com commit** — o commit acontece aqui, e não na
   dependência `get_db`, para que o evento não seja publicado antes do pedido
   existir de fato;
3. só então o evento `PedidoCriado` vai para o SQS.

Se a persistência falhar depois da reserva, o estoque é **compensado**
(`ReleaseReserva` de tudo que foi reservado) e nenhum evento é publicado.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.estoque_client import EstoqueClient, StatusReserva
from app.clients.sqs_client import SQSClient
from app.models.enums import StatusPedido
from app.models.pedido import Pedido
from app.repositories.pedido_repository import PedidoRepository
from app.schemas.pedido import PedidoCreate

logger = logging.getLogger(__name__)

_NAMESPACE_PEDIDO = "caixanamao/pedidos"

# Publicações de eventos em voo (best-effort). Ficam registradas para não serem
# coletadas pelo GC no meio do envio e para poderem ser aguardadas por
# `aguardar_publicacoes()` (testes e shutdown gracioso).
_tarefas_publicacao: set[asyncio.Task[None]] = set()


def _agendar_publicacao(funcao, **kwargs) -> None:
    """Agenda a publicação (síncrona, boto3) em thread e **não** aguarda.

    Publicar dentro do handler somaria a latência do broker à resposta do POST
    (com a fila inalcançável, ~7s medidos) e ainda bloquearia o event loop, já
    que o cliente boto3 é síncrono. A operação está commitada e a publicação é
    best-effort (ADR-005): o que importa é a ordem (depois do commit), não a
    espera.
    """
    tarefa = asyncio.create_task(asyncio.to_thread(funcao, **kwargs))
    _tarefas_publicacao.add(tarefa)
    tarefa.add_done_callback(_tarefas_publicacao.discard)


async def aguardar_publicacoes() -> None:
    """Aguarda as publicações de eventos em voo (shutdown gracioso e testes)."""
    while _tarefas_publicacao:
        await asyncio.gather(*list(_tarefas_publicacao), return_exceptions=True)
        # `gather` sobre tarefas já concluídas retorna sem ceder o controle: sem este
        # `sleep(0)` o laço giraria em busy loop antes de o callback de conclusão
        # (que limpa o conjunto) rodar.
        await asyncio.sleep(0)


def pedido_id_idempotente(chave: str) -> uuid.UUID:
    """Deriva um `pedido_id` determinístico a partir da chave do cliente.

    Mesma chave ⇒ mesmo `pedido_id` ⇒ o servico-estoque deduplica a reserva pelo
    par (pedido_id, item_id) e o retry do cliente (timeout de rede) não reserva
    estoque nem cria pedido em duplicidade.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{_NAMESPACE_PEDIDO}/{chave}")


class PedidoService:
    def __init__(
        self,
        db: AsyncSession,
        estoque: EstoqueClient,
        sqs: SQSClient,
    ) -> None:
        self._db = db
        self._repo = PedidoRepository(db)
        self._estoque = estoque
        self._sqs = sqs

    async def criar_pedido(
        self,
        vendedor_id: uuid.UUID,
        payload: PedidoCreate,
        idempotency_key: str | None = None,
    ) -> Pedido:
        """Fluxo completo de criação de pedido (arquitetura §3.2).

        1. Valida itens (feito nos schemas Pydantic)
        2. Reserva estoque via gRPC para cada item
        3. Persiste o pedido como CONFIRMADO (commit explícito)
        4. Publica evento PedidoCriado no SQS (best-effort, após o commit)
        """
        pedido_id = pedido_id_idempotente(idempotency_key) if idempotency_key else uuid.uuid4()

        # Retry do mesmo cliente (mesma Idempotency-Key) devolve o pedido já criado.
        if idempotency_key is not None:
            existente = await self._repo.get_by_id(pedido_id)
            if existente is not None:
                if existente.vendedor_id != vendedor_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency-Key já utilizada por outro vendedor",
                    )
                logger.info(
                    "Retry idempotente do pedido %s (Idempotency-Key reapresentada)", pedido_id
                )
                return existente

        total = sum(
            (Decimal(str(item.preco_unitario)) * item.quantidade for item in payload.itens),
            Decimal(0),
        )

        # 1. Reserva todos os itens — se qualquer um falhar, compensa os anteriores.
        reservas_feitas: list[tuple[uuid.UUID, int, uuid.UUID]] = []
        try:
            for item in payload.itens:
                result = await self._estoque.check_and_reserve(
                    item_id=item.item_id,
                    quantidade=item.quantidade,
                    pedido_id=pedido_id,
                    request_uuid=uuid.uuid4(),
                )

                if not result.sucesso:
                    await self._release_all(reservas_feitas)
                    _raise_for_reserva_status(result.status, item.item_id)

                reservas_feitas.append((item.item_id, item.quantidade, pedido_id))

        except HTTPException:
            raise
        except Exception as exc:
            await self._release_all(reservas_feitas)
            logger.error("Erro inesperado ao reservar estoque: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Serviço de estoque temporariamente indisponível",
            ) from exc

        # 2. Persiste e faz o COMMIT antes de publicar qualquer evento.
        try:
            pedido = await self._repo.create(
                vendedor_id=vendedor_id,
                itens=payload.itens,
                total=total,
                pedido_id=pedido_id,
            )
            await self._db.commit()
        except IntegrityError as exc:
            # Corrida de idempotência: outra requisição com a mesma chave gravou o
            # pedido primeiro. Devolve o que existe (as reservas são idempotentes).
            await self._db.rollback()
            existente = await self._repo.get_by_id(pedido_id)
            if existente is not None and existente.vendedor_id == vendedor_id:
                logger.info("Pedido %s criado por requisição concorrente (idempotente)", pedido_id)
                return existente
            await self._release_all(reservas_feitas)
            logger.error("Falha de integridade ao persistir o pedido %s: %s", pedido_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Falha ao persistir o pedido",
            ) from exc
        except SQLAlchemyError as exc:
            # Sem pedido no banco não pode haver estoque preso: compensa.
            await self._db.rollback()
            await self._release_all(reservas_feitas)
            logger.exception("Falha ao persistir o pedido %s — estoque compensado", pedido_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Falha ao persistir o pedido; estoque liberado",
            ) from exc

        # 3. Publica o evento (best-effort) — só depois do commit e SEM bloquear a
        #    resposta: o envio do boto3 é síncrono e roda em thread (ver
        #    `_agendar_publicacao`).
        _agendar_publicacao(
            self._sqs.publish_pedido_criado,
            pedido_id=pedido.id,
            vendedor_id=vendedor_id,
            total=str(total),
            itens=[
                {
                    "item_id": str(i.item_id),
                    "quantidade": i.quantidade,
                    "preco_unitario": str(i.preco_unitario),
                }
                for i in payload.itens
            ],
        )

        logger.info("Pedido criado com sucesso: id=%s vendedor=%s", pedido.id, vendedor_id)
        return pedido

    async def cancelar_pedido(
        self,
        pedido_id: uuid.UUID,
        vendedor_id: uuid.UUID,
    ) -> Pedido:
        """Cancela um pedido e libera o estoque reservado via gRPC."""
        pedido = await self._repo.get_by_id(pedido_id)

        if pedido is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado")

        if pedido.vendedor_id != vendedor_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão")

        if pedido.status == StatusPedido.CANCELADO:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pedido já cancelado")

        # Libera estoque para cada item
        for item in pedido.itens:
            result = await self._estoque.release_reserva(
                item_id=item.item_id,
                quantidade=item.quantidade,
                pedido_id=pedido_id,
            )
            if not result.sucesso:
                logger.warning(
                    "Falha ao liberar reserva (item=%s pedido=%s): %s",
                    item.item_id,
                    pedido_id,
                    result.mensagem,
                )
                # Não bloqueia o cancelamento — registra e segue (débito: compensação futura)

        pedido = await self._repo.update_status(pedido, StatusPedido.CANCELADO)
        await self._db.commit()
        return pedido

    async def _release_all(
        self, reservas: list[tuple[uuid.UUID, int, uuid.UUID]]
    ) -> None:
        """Compensação: libera todas as reservas já feitas para um pedido que falhou."""
        for item_id, quantidade, pedido_id in reservas:
            await self._estoque.release_reserva(
                item_id=item_id, quantidade=quantidade, pedido_id=pedido_id
            )


def _raise_for_reserva_status(reserva_status: StatusReserva, item_id: uuid.UUID) -> None:
    """Converte status de reserva gRPC em HTTPException apropriada."""
    if reserva_status == StatusReserva.ESTOQUE_INSUFICIENTE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Estoque insuficiente para o item {item_id}",
        )
    elif reserva_status == StatusReserva.ESTOQUE_BLOQUEADO:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Item {item_id} momentaneamente bloqueado, tente novamente",
            headers={"Retry-After": "1"},
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Erro ao comunicar com o serviço de estoque",
        )
