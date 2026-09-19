"""Testes de domínio: reserva, liberação, idempotência e fail-closed."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.locking.redis_lock import RedisLockManager
from app.models.enums import TipoMovimentacao
from app.models.movimentacao import Movimentacao
from app.services.estoque_service import (
    EstoqueService,
    StatusLiberacao,
    StatusReserva,
)


class RedisIndisponivel:
    """Redis que não responde — usado para provar o fail closed do ADR-004."""

    async def set(self, *args, **kwargs):
        raise ConnectionError("Connection refused")

    async def eval(self, *args, **kwargs):
        raise ConnectionError("Connection refused")


async def _contar_movimentacoes(session, pedido_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(Movimentacao)
        .where(Movimentacao.pedido_id == pedido_id)
    )


# ---------------------------------------------------------------------------
# Reserva — caminho feliz
# ---------------------------------------------------------------------------
async def test_reserva_confirmada_debita_saldo_registra_movimentacao_e_libera_lock(
    session, lock_manager, item_factory, publisher, redis_fake
):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager, publisher)

    resultado = await servico.reservar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusReserva.CONFIRMADO
    assert resultado.sucesso is True
    assert resultado.item is not None and resultado.item.quantidade_disponivel == 7

    await session.refresh(item)
    assert item.quantidade_disponivel == 7
    assert item.quantidade_reservada == 3

    movimentacao = await session.scalar(
        select(Movimentacao).where(Movimentacao.pedido_id == pedido_id)
    )
    assert movimentacao is not None
    assert movimentacao.tipo == TipoMovimentacao.RESERVA.value
    assert movimentacao.quantidade == 3

    # Lock liberado ao final (script Lua) — não fica preso até o TTL.
    assert await redis_fake.get(f"lock:estoque:{item.id}") is None

    # Evento EstoqueAtualizado publicado (best-effort) com event_id (at-least-once).
    assert len(publisher.eventos) == 1
    evento = publisher.eventos[0]
    assert evento["evento"] == "EstoqueAtualizado"
    assert evento["tipo_movimentacao"] == "RESERVA"
    assert evento["event_id"]
    assert evento["quantidade_disponivel"] == 7


async def test_reserva_sem_saldo_retorna_estoque_insuficiente_sem_alterar_saldos(
    session, lock_manager, item_factory
):
    item = await item_factory(quantidade=1)
    servico = EstoqueService(session, lock_manager)

    resultado = await servico.reservar(
        item_id=item.id, quantidade=2, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusReserva.ESTOQUE_INSUFICIENTE
    assert resultado.sucesso is False
    await session.refresh(item)
    assert item.quantidade_disponivel == 1
    assert item.quantidade_reservada == 0


async def test_ultimo_item_nao_pode_ser_reservado_duas_vezes(session, lock_manager, item_factory):
    """A guarda é a condição transacional no UPDATE, não o TTL do lock."""
    item = await item_factory(quantidade=1)
    servico = EstoqueService(session, lock_manager)

    primeira = await servico.reservar(
        item_id=item.id, quantidade=1, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )
    segunda = await servico.reservar(
        item_id=item.id, quantidade=1, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert primeira.status is StatusReserva.CONFIRMADO
    assert segunda.status is StatusReserva.ESTOQUE_INSUFICIENTE
    await session.refresh(item)
    assert item.quantidade_disponivel == 0
    assert item.quantidade_reservada == 1


async def test_reserva_de_item_inexistente(session, lock_manager):
    servico = EstoqueService(session, lock_manager)

    resultado = await servico.reservar(
        item_id=uuid.uuid4(), quantidade=1, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusReserva.ITEM_NAO_ENCONTRADO


# ---------------------------------------------------------------------------
# Idempotência
# ---------------------------------------------------------------------------
async def test_retry_do_mesmo_pedido_nao_debita_duas_vezes(session, lock_manager, item_factory):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)

    primeira = await servico.reservar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    segunda = await servico.reservar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert primeira.status is StatusReserva.CONFIRMADO
    assert segunda.status is StatusReserva.CONFIRMADO
    assert "idempotente" in segunda.mensagem
    await session.refresh(item)
    assert item.quantidade_disponivel == 7
    assert item.quantidade_reservada == 3
    assert await _contar_movimentacoes(session, pedido_id) == 1


async def test_retry_do_mesmo_pedido_com_parametros_diferentes_e_conflito(
    session, lock_manager, item_factory
):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)

    await servico.reservar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    divergente = await servico.reservar(
        item_id=item.id, quantidade=5, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert divergente.status is StatusReserva.CONFLITO_IDEMPOTENCIA
    await session.refresh(item)
    assert item.quantidade_disponivel == 7


# ---------------------------------------------------------------------------
# Lock e fail closed
# ---------------------------------------------------------------------------
async def test_lock_ocupado_retorna_estoque_bloqueado(
    session, lock_manager, item_factory, redis_fake
):
    item = await item_factory(quantidade=10)
    # Outra instância detém o lock do item.
    await redis_fake.set(f"lock:estoque:{item.id}", str(uuid.uuid4()), nx=True, ex=5)
    servico = EstoqueService(session, lock_manager)

    resultado = await servico.reservar(
        item_id=item.id, quantidade=1, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusReserva.ESTOQUE_BLOQUEADO
    await session.refresh(item)
    assert item.quantidade_disponivel == 10
    assert item.quantidade_reservada == 0


async def test_redis_indisponivel_falha_fechado(session, item_factory):
    """ADR-004: sem Redis, a reserva é recusada — nunca degrada para o banco."""
    item = await item_factory(quantidade=10)
    lock_com_redis_fora = RedisLockManager(RedisIndisponivel(), ttl_seconds=5)
    servico = EstoqueService(session, lock_com_redis_fora)

    resultado = await servico.reservar(
        item_id=item.id, quantidade=1, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusReserva.ESTOQUE_BLOQUEADO
    assert "fail closed" in resultado.mensagem
    await session.refresh(item)
    assert item.quantidade_disponivel == 10
    assert item.quantidade_reservada == 0


# ---------------------------------------------------------------------------
# Liberação
# ---------------------------------------------------------------------------
async def test_liberacao_devolve_saldo_e_e_idempotente(session, lock_manager, item_factory, publisher):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager, publisher)

    await servico.reservar(
        item_id=item.id, quantidade=4, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    liberacao = await servico.liberar(
        item_id=item.id, quantidade=4, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    repetida = await servico.liberar(
        item_id=item.id, quantidade=4, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert liberacao.status is StatusLiberacao.APLICADA
    assert liberacao.sucesso is True
    assert repetida.status is StatusLiberacao.JA_APLICADA
    assert repetida.sucesso is True

    await session.refresh(item)
    assert item.quantidade_disponivel == 10
    assert item.quantidade_reservada == 0

    movimentacoes = (
        await session.scalars(
            select(Movimentacao).where(Movimentacao.pedido_id == pedido_id).order_by(Movimentacao.tipo)
        )
    ).all()
    assert [m.tipo for m in movimentacoes] == ["LIBERACAO", "RESERVA"]
    # Um evento por movimentação efetiva (a liberação repetida não publica de novo).
    assert len(publisher.eventos) == 2


async def test_liberacao_sem_reserva_registrada(session, lock_manager, item_factory):
    item = await item_factory(quantidade=10)
    servico = EstoqueService(session, lock_manager)

    resultado = await servico.liberar(
        item_id=item.id, quantidade=1, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusLiberacao.RESERVA_NAO_ENCONTRADA
    assert resultado.sucesso is False


async def test_liberacao_com_parametros_divergentes(session, lock_manager, item_factory):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)
    await servico.reservar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    resultado = await servico.liberar(
        item_id=item.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusLiberacao.PARAMETROS_DIVERGENTES
    await session.refresh(item)
    assert item.quantidade_reservada == 3


async def test_liberacao_com_redis_fora_falha_fechado(session, lock_manager, item_factory):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)
    await servico.reservar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    servico_sem_redis = EstoqueService(session, RedisLockManager(RedisIndisponivel()))
    resultado = await servico_sem_redis.liberar(
        item_id=item.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusLiberacao.ESTOQUE_BLOQUEADO
    await session.refresh(item)
    assert item.quantidade_reservada == 3


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
async def test_consulta_item(session, lock_manager, item_factory):
    item = await item_factory(quantidade=7, nome="Pastel")
    servico = EstoqueService(session, lock_manager)

    encontrado = await servico.consultar(item.id)
    inexistente = await servico.consultar(uuid.uuid4())

    assert encontrado is not None
    assert encontrado.nome == "Pastel"
    assert encontrado.quantidade_disponivel == 7
    assert encontrado.quantidade_reservada == 0
    assert inexistente is None
