"""Serviço de domínio do estoque: reserva, liberação e consulta.

Fluxo de reserva implementado exatamente como especificado em
`docs/arquitetura.md` §3.3 e no ADR-004:

1. checagem de idempotência no ledger (`movimentacoes`);
2. aquisição do lock distribuído `SET lock:estoque:{item_id} {request_uuid} NX EX 5`
   (falha do Redis ⇒ **fail closed** ⇒ `ESTOQUE_BLOQUEADO`);
3. transação PostgreSQL com **UPDATE condicional atômico**
   (`WHERE quantidade_disponivel >= :quantidade`) + linha em `movimentacoes`;
4. liberação do lock via Lua com checagem de propriedade;
5. publicação best-effort do evento `EstoqueAtualizado`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.event_publisher import EventPublisher, NullEventPublisher, novo_evento_estoque_atualizado
from app.locking.redis_lock import LockIndisponivelError, RedisLockManager
from app.models.enums import TipoMovimentacao
from app.models.item import Item
from app.repositories.estoque_repository import EstoqueRepository

logger = logging.getLogger(__name__)


class StatusReserva(str, Enum):
    """Resultado de uma reserva.

    `CONFIRMADO`, `ESTOQUE_INSUFICIENTE` e `ESTOQUE_BLOQUEADO` são exatamente os
    valores do contrato gRPC. `ITEM_NAO_ENCONTRADO`, `CONFLITO_IDEMPOTENCIA` e
    `QUANTIDADE_INVALIDA` são internos: o contrato não tem campo para expressá-los,
    então o servicer os traduz para os status gRPC `NOT_FOUND`,
    `FAILED_PRECONDITION` e `INVALID_ARGUMENT`.
    """

    CONFIRMADO = "CONFIRMADO"
    ESTOQUE_INSUFICIENTE = "ESTOQUE_INSUFICIENTE"
    ESTOQUE_BLOQUEADO = "ESTOQUE_BLOQUEADO"
    ITEM_NAO_ENCONTRADO = "ITEM_NAO_ENCONTRADO"
    CONFLITO_IDEMPOTENCIA = "CONFLITO_IDEMPOTENCIA"
    QUANTIDADE_INVALIDA = "QUANTIDADE_INVALIDA"


class StatusLiberacao(str, Enum):
    """Resultado de uma liberação (o contrato só expõe `sucesso` + `mensagem`)."""

    APLICADA = "APLICADA"
    JA_APLICADA = "JA_APLICADA"
    RESERVA_NAO_ENCONTRADA = "RESERVA_NAO_ENCONTRADA"
    PARAMETROS_DIVERGENTES = "PARAMETROS_DIVERGENTES"
    ESTOQUE_BLOQUEADO = "ESTOQUE_BLOQUEADO"
    INCONSISTENCIA = "INCONSISTENCIA"
    QUANTIDADE_INVALIDA = "QUANTIDADE_INVALIDA"


def quantidade_valida(quantidade: object) -> bool:
    """Quantidade precisa ser inteiro maior que zero.

    Validação na camada de domínio (e não só no servicer): uma quantidade
    negativa passaria pelo `UPDATE ... WHERE quantidade_disponivel >= :q` e
    *aumentaria* o saldo — corrompendo o estoque. Vale para qualquer chamador
    futuro, não apenas para o caminho gRPC.
    """
    return isinstance(quantidade, int) and not isinstance(quantidade, bool) and quantidade > 0


@dataclass(frozen=True)
class ItemResumo:
    """Projeção imutável de um item (usada nas respostas gRPC)."""

    id: uuid.UUID
    nome: str
    quantidade_disponivel: int
    quantidade_reservada: int

    @classmethod
    def de_item(cls, item: Item) -> ItemResumo:
        return cls(
            id=item.id,
            nome=item.nome,
            quantidade_disponivel=item.quantidade_disponivel,
            quantidade_reservada=item.quantidade_reservada,
        )


@dataclass(frozen=True)
class ResultadoReserva:
    status: StatusReserva
    mensagem: str
    item: ItemResumo | None = None

    @property
    def sucesso(self) -> bool:
        return self.status is StatusReserva.CONFIRMADO


@dataclass(frozen=True)
class ResultadoLiberacao:
    status: StatusLiberacao
    mensagem: str
    item: ItemResumo | None = None

    @property
    def sucesso(self) -> bool:
        return self.status in (StatusLiberacao.APLICADA, StatusLiberacao.JA_APLICADA)


class EstoqueService:
    """Orquestra lock + transação + ledger para uma unidade de trabalho."""

    def __init__(
        self,
        session: AsyncSession,
        lock_manager: RedisLockManager,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._session = session
        self._lock = lock_manager
        self._publisher: EventPublisher = publisher or NullEventPublisher()
        self._repo = EstoqueRepository(session)

    # ------------------------------------------------------------------
    # Reserva
    # ------------------------------------------------------------------
    async def reservar(
        self,
        *,
        item_id: uuid.UUID,
        quantidade: int,
        pedido_id: uuid.UUID,
        request_uuid: uuid.UUID,
    ) -> ResultadoReserva:
        # 0. Entrada inválida nunca chega ao banco (quantidade negativa aumentaria o saldo).
        if not quantidade_valida(quantidade):
            logger.warning("Reserva recusada: quantidade inválida (%r)", quantidade)
            return ResultadoReserva(
                StatusReserva.QUANTIDADE_INVALIDA,
                f"quantidade deve ser um inteiro maior que zero (recebido: {quantidade!r})",
            )

        # 1. Idempotência: retry do mesmo pedido não reserva duas vezes.
        reserva_existente = await self._repo.buscar_movimentacao(pedido_id, TipoMovimentacao.RESERVA)
        if reserva_existente is not None:
            if reserva_existente.item_id != item_id or reserva_existente.quantidade != quantidade:
                logger.warning(
                    "Retry do pedido %s com parâmetros divergentes da reserva registrada", pedido_id
                )
                return ResultadoReserva(
                    StatusReserva.CONFLITO_IDEMPOTENCIA,
                    "Pedido já possui reserva registrada com item/quantidade diferentes",
                )
            item = await self._repo.buscar_item(item_id)
            logger.info("Reserva idempotente (pedido %s já reservado)", pedido_id)
            return ResultadoReserva(
                StatusReserva.CONFIRMADO,
                "Reserva já registrada para este pedido (idempotente)",
                ItemResumo.de_item(item) if item else None,
            )

        # 2. Lock distribuído — fail closed quando o Redis não responde.
        try:
            adquirido = await self._lock.adquirir(item_id, request_uuid)
        except LockIndisponivelError as exc:
            logger.error("Reserva recusada: %s", exc)
            return ResultadoReserva(
                StatusReserva.ESTOQUE_BLOQUEADO,
                "Serviço de locking indisponível; reserva recusada (fail closed)",
            )
        if not adquirido:
            logger.info("Item %s já está bloqueado por outra requisição", item_id)
            return ResultadoReserva(
                StatusReserva.ESTOQUE_BLOQUEADO,
                "Item bloqueado por outra operação; tente novamente",
            )

        # 3. Transação atômica
        try:
            item = await self._repo.reservar_atomico(item_id, quantidade)
            if item is None:
                await self._session.rollback()
                if await self._repo.buscar_item(item_id) is None:
                    return ResultadoReserva(
                        StatusReserva.ITEM_NAO_ENCONTRADO, f"Item {item_id} não encontrado"
                    )
                return ResultadoReserva(
                    StatusReserva.ESTOQUE_INSUFICIENTE,
                    f"Saldo insuficiente para reservar {quantidade} unidade(s) do item {item_id}",
                )
            await self._repo.registrar_movimentacao(
                item_id, TipoMovimentacao.RESERVA, quantidade, pedido_id
            )
            await self._session.commit()
            resumo = ItemResumo.de_item(item)
        except IntegrityError:
            # Corrida de idempotência: outra transação/concorrente já registrou a
            # reserva deste pedido (unique pedido_id+tipo). Reverte e confirma.
            await self._session.rollback()
            logger.info("Idempotência acionada por corrida concorrente (pedido %s)", pedido_id)
            item = await self._repo.buscar_item(item_id)
            return ResultadoReserva(
                StatusReserva.CONFIRMADO,
                "Reserva já registrada para este pedido (idempotente)",
                ItemResumo.de_item(item) if item else None,
            )
        except SQLAlchemyError:
            await self._session.rollback()
            logger.exception("Falha ao persistir reserva do item %s", item_id)
            raise
        finally:
            # 4. Liberação do lock com checagem de propriedade (ADR-004).
            await self._liberar_lock(item_id, request_uuid)

        # 5. Evento best-effort (nunca bloqueia a operação já commitada).
        await self._publicar(
            TipoMovimentacao.RESERVA, item_id, pedido_id, quantidade, resumo
        )
        return ResultadoReserva(StatusReserva.CONFIRMADO, "Estoque reservado com sucesso", resumo)

    # ------------------------------------------------------------------
    # Liberação (cancelamento)
    # ------------------------------------------------------------------
    async def liberar(
        self,
        *,
        item_id: uuid.UUID,
        quantidade: int,
        pedido_id: uuid.UUID,
        request_uuid: uuid.UUID,
    ) -> ResultadoLiberacao:
        if not quantidade_valida(quantidade):
            logger.warning("Liberação recusada: quantidade inválida (%r)", quantidade)
            return ResultadoLiberacao(
                StatusLiberacao.QUANTIDADE_INVALIDA,
                f"quantidade deve ser um inteiro maior que zero (recebido: {quantidade!r})",
            )

        if await self._repo.buscar_movimentacao(pedido_id, TipoMovimentacao.LIBERACAO) is not None:
            logger.info("Liberação idempotente (pedido %s já liberado)", pedido_id)
            return ResultadoLiberacao(
                StatusLiberacao.JA_APLICADA, "Liberação já aplicada para este pedido (idempotente)"
            )

        reserva = await self._repo.buscar_movimentacao(pedido_id, TipoMovimentacao.RESERVA)
        if reserva is None:
            return ResultadoLiberacao(
                StatusLiberacao.RESERVA_NAO_ENCONTRADA,
                "Nenhuma reserva registrada para este pedido",
            )
        if reserva.item_id != item_id or reserva.quantidade != quantidade:
            return ResultadoLiberacao(
                StatusLiberacao.PARAMETROS_DIVERGENTES,
                "Item/quantidade divergem da reserva registrada para o pedido",
            )

        try:
            adquirido = await self._lock.adquirir(item_id, request_uuid)
        except LockIndisponivelError as exc:
            logger.error("Liberação recusada: %s", exc)
            return ResultadoLiberacao(
                StatusLiberacao.ESTOQUE_BLOQUEADO,
                "Serviço de locking indisponível; liberação recusada (fail closed)",
            )
        if not adquirido:
            return ResultadoLiberacao(
                StatusLiberacao.ESTOQUE_BLOQUEADO,
                "Item bloqueado por outra operação; tente novamente",
            )

        try:
            item = await self._repo.liberar_atomico(item_id, quantidade)
            if item is None:
                await self._session.rollback()
                return ResultadoLiberacao(
                    StatusLiberacao.INCONSISTENCIA,
                    "Saldo reservado insuficiente para liberar a quantidade informada",
                )
            await self._repo.registrar_movimentacao(
                item_id, TipoMovimentacao.LIBERACAO, quantidade, pedido_id
            )
            await self._session.commit()
            resumo = ItemResumo.de_item(item)
        except IntegrityError:
            await self._session.rollback()
            return ResultadoLiberacao(
                StatusLiberacao.JA_APLICADA, "Liberação já aplicada para este pedido (idempotente)"
            )
        except SQLAlchemyError:
            await self._session.rollback()
            logger.exception("Falha ao persistir liberação do item %s", item_id)
            raise
        finally:
            await self._liberar_lock(item_id, request_uuid)

        await self._publicar(TipoMovimentacao.LIBERACAO, item_id, pedido_id, quantidade, resumo)
        return ResultadoLiberacao(StatusLiberacao.APLICADA, "Estoque liberado com sucesso", resumo)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    async def consultar(self, item_id: uuid.UUID) -> ItemResumo | None:
        item = await self._repo.buscar_item(item_id)
        return ItemResumo.de_item(item) if item is not None else None

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    async def _liberar_lock(self, item_id: uuid.UUID, request_uuid: uuid.UUID) -> None:
        """Libera o lock. Falha aqui não invalida a operação já commitada."""
        try:
            if not await self._lock.liberar(item_id, request_uuid):
                logger.warning(
                    "Lock de %s não pertencia mais a esta requisição na liberação "
                    "(TTL de %ss expirado durante a operação)",
                    item_id,
                    self._lock.ttl_seconds,
                )
        except LockIndisponivelError as exc:
            logger.warning(
                "Não foi possível liberar o lock de %s (%s); o TTL de %ss o fará",
                item_id,
                exc,
                self._lock.ttl_seconds,
            )

    async def _publicar(
        self,
        tipo: TipoMovimentacao,
        item_id: uuid.UUID,
        pedido_id: uuid.UUID,
        quantidade: int,
        resumo: ItemResumo,
    ) -> None:
        evento = novo_evento_estoque_atualizado(
            tipo_movimentacao=tipo.value,
            item_id=item_id,
            pedido_id=pedido_id,
            quantidade=quantidade,
            quantidade_disponivel=resumo.quantidade_disponivel,
            quantidade_reservada=resumo.quantidade_reservada,
        )
        await self._publisher.publicar_estoque_atualizado(evento=evento)
