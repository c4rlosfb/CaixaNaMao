"""Testes de domínio: reserva, liberação, idempotência e fail-closed."""

from __future__ import annotations

import uuid

import pytest
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


# ---------------------------------------------------------------------------
# Validação de entrada no domínio (quantidade precisa ser inteiro > 0)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("quantidade", [0, -1, -5])
async def test_reserva_com_quantidade_invalida_nao_altera_saldo(
    session, lock_manager, item_factory, redis_fake, quantidade
):
    """Quantidade 0/negativa não pode chegar ao UPDATE (a negativa aumentaria o saldo)."""
    item = await item_factory(quantidade=10)
    servico = EstoqueService(session, lock_manager)

    resultado = await servico.reservar(
        item_id=item.id, quantidade=quantidade, pedido_id=uuid.uuid4(), request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusReserva.QUANTIDADE_INVALIDA
    assert resultado.sucesso is False
    await session.refresh(item)
    assert item.quantidade_disponivel == 10
    assert item.quantidade_reservada == 0
    # A validação acontece antes de qualquer efeito: nem lock é adquirido.
    assert await redis_fake.keys("lock:*") == []


@pytest.mark.parametrize("quantidade", [0, -3])
async def test_liberacao_com_quantidade_invalida_nao_altera_saldo(
    session, lock_manager, item_factory, quantidade
):
    item = await item_factory(quantidade=10)
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)
    await servico.reservar(
        item_id=item.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    resultado = await servico.liberar(
        item_id=item.id, quantidade=quantidade, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert resultado.status is StatusLiberacao.QUANTIDADE_INVALIDA
    assert resultado.sucesso is False
    await session.refresh(item)
    assert item.quantidade_disponivel == 8
    assert item.quantidade_reservada == 2


# ---------------------------------------------------------------------------
# Pedido com múltiplos itens (regressão do bloqueador apontado no review)
# O `criar_pedido` do servico-pedidos chama CheckAndReserve UMA VEZ POR ITEM,
# sempre com o mesmo `pedido_id`.
# ---------------------------------------------------------------------------
async def test_pedido_com_multiplos_itens_reserva_todos_os_itens(session, lock_manager, item_factory):
    item_a = await item_factory(quantidade=10, nome="Coxinha")
    item_b = await item_factory(quantidade=5, nome="Pastel")
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)

    reserva_a = await servico.reservar(
        item_id=item_a.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    reserva_b = await servico.reservar(
        item_id=item_b.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    # Antes da correção, o 2º item recebia CONFLITO_IDEMPOTENCIA (FAILED_PRECONDITION
    # no gRPC) e qualquer pedido com 2+ itens falhava.
    assert reserva_a.status is StatusReserva.CONFIRMADO
    assert reserva_b.status is StatusReserva.CONFIRMADO

    await session.refresh(item_a)
    await session.refresh(item_b)
    assert (item_a.quantidade_disponivel, item_a.quantidade_reservada) == (8, 2)
    assert (item_b.quantidade_disponivel, item_b.quantidade_reservada) == (2, 3)

    movimentacoes = (
        await session.scalars(select(Movimentacao).where(Movimentacao.pedido_id == pedido_id))
    ).all()
    assert sorted(m.item_id for m in movimentacoes) == sorted([item_a.id, item_b.id])


async def test_cancelamento_de_pedido_com_multiplos_itens_devolve_todos(session, lock_manager, item_factory):
    item_a = await item_factory(quantidade=10, nome="Coxinha")
    item_b = await item_factory(quantidade=5, nome="Pastel")
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)
    await servico.reservar(
        item_id=item_a.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    await servico.reservar(
        item_id=item_b.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    liberacao_a = await servico.liberar(
        item_id=item_a.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    liberacao_b = await servico.liberar(
        item_id=item_b.id, quantidade=3, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    # Antes da correção, a 2ª liberação caía em JA_APLICADA sem devolver o estoque.
    assert liberacao_a.status is StatusLiberacao.APLICADA
    assert liberacao_b.status is StatusLiberacao.APLICADA

    await session.refresh(item_a)
    await session.refresh(item_b)
    assert (item_a.quantidade_disponivel, item_a.quantidade_reservada) == (10, 0)
    assert (item_b.quantidade_disponivel, item_b.quantidade_reservada) == (5, 0)


async def test_retry_do_mesmo_item_segue_idempotente_com_outro_item_no_pedido(
    session, lock_manager, item_factory
):
    """A chave por item não pode afrouxar a idempotência do retry do mesmo item."""
    item = await item_factory(quantidade=10, nome="Coxinha")
    outro_item = await item_factory(quantidade=10, nome="Pastel")
    pedido_id = uuid.uuid4()
    servico = EstoqueService(session, lock_manager)
    await servico.reservar(
        item_id=item.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    await servico.reservar(
        item_id=outro_item.id, quantidade=1, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    retry = await servico.reservar(
        item_id=item.id, quantidade=2, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )
    divergente = await servico.reservar(
        item_id=item.id, quantidade=5, pedido_id=pedido_id, request_uuid=uuid.uuid4()
    )

    assert retry.status is StatusReserva.CONFIRMADO
    assert "idempotente" in retry.mensagem
    assert divergente.status is StatusReserva.CONFLITO_IDEMPOTENCIA
    await session.refresh(item)
    assert (item.quantidade_disponivel, item.quantidade_reservada) == (8, 2)
