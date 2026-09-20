"""Testes unitários do PedidoService — API real, com banco de teste e mocks apenas
dos clientes externos (gRPC/SQS).

Cobrem a ordem dos efeitos externos (reserva → commit → evento) e a compensação
de estoque — bloqueadores do review do PR #26.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clients.estoque_client import EstoqueClient, ReservaResult, StatusReserva
from app.clients.sqs_client import SQSClient
from app.models.enums import StatusPedido
from app.models.pedido import Pedido
from app.repositories.pedido_repository import PedidoRepository
from app.schemas.pedido import ItemPedidoCreate, PedidoCreate
from app.services.pedido_service import (
    PedidoService,
    aguardar_publicacoes,
    pedido_id_idempotente,
)


def _dto(*itens: tuple[uuid.UUID, int, str]) -> PedidoCreate:
    """Monta o payload de criação de pedido."""
    return PedidoCreate(
        itens=[
            ItemPedidoCreate(item_id=item_id, quantidade=quantidade, preco_unitario=Decimal(preco))
            for item_id, quantidade, preco in itens
        ]
    )


@pytest.fixture
def service(db_session, mock_estoque_ok: EstoqueClient, mock_sqs: SQSClient) -> PedidoService:
    """Serviço de produção apontando para o banco de teste."""
    return PedidoService(db=db_session, estoque=mock_estoque_ok, sqs=mock_sqs)


async def _criar_pedido_persistido(
    db_session, vendedor_id: uuid.UUID, item_id: uuid.UUID
) -> Pedido:
    """Cria um pedido no banco de teste (usado nos cenários de cancelamento)."""
    repo = PedidoRepository(db_session)
    pedido = await repo.create(
        vendedor_id=vendedor_id,
        itens=[ItemPedidoCreate(item_id=item_id, quantidade=2, preco_unitario=Decimal("25.00"))],
        total=Decimal("50.00"),
    )
    await db_session.commit()
    return pedido


# ---------------------------------------------------------------------------
# Criação: persistência, precisão do total e publicação do evento
# ---------------------------------------------------------------------------
async def test_criar_pedido_persiste_publica_evento_e_mantem_decimal(
    service, db_session, mock_estoque_ok, mock_sqs
):
    vendedor_id = uuid.uuid4()
    item_1, item_2 = uuid.uuid4(), uuid.uuid4()

    pedido = await service.criar_pedido(
        vendedor_id=vendedor_id,
        payload=_dto((item_1, 2, "10.00"), (item_2, 1, "0.10")),
    )

    assert pedido.status == StatusPedido.CONFIRMADO
    # Total exato em Decimal (com float, 2*10.00 + 0.10 perderia precisão)
    assert pedido.total == Decimal("20.10")
    assert mock_estoque_ok.check_and_reserve.await_count == 2

    # A publicação é agendada (não bloqueia a resposta): aguarda o flush para
    # inspecionar o evento.
    await aguardar_publicacoes()

    # Evento publicado UMA vez, com o total serializado a partir do Decimal
    mock_sqs.publish_pedido_criado.assert_called_once()
    kwargs = mock_sqs.publish_pedido_criado.call_args.kwargs
    assert kwargs["pedido_id"] == pedido.id
    assert kwargs["total"] == "20.10"
    assert len(kwargs["itens"]) == 2

    # O commit aconteceu dentro do serviço (o pedido está no banco)
    persistido = await db_session.scalar(select(Pedido).where(Pedido.id == pedido.id))
    assert persistido is not None


async def test_commit_acontece_antes_da_publicacao(
    service, db_session, mock_estoque_ok, mock_sqs, monkeypatch
):
    """B6: o evento PedidoCriado só pode sair depois do pedido estar commitado."""
    ordem: list[str] = []
    commit_original = db_session.commit

    async def _commit_espiao() -> None:
        await commit_original()
        ordem.append("commit")

    def _publish_espiao(**kwargs) -> bool:
        ordem.append("publish")
        return True

    monkeypatch.setattr(db_session, "commit", _commit_espiao)
    mock_sqs.publish_pedido_criado = MagicMock(side_effect=_publish_espiao)

    await service.criar_pedido(vendedor_id=uuid.uuid4(), payload=_dto((uuid.uuid4(), 1, "5.00")))
    await aguardar_publicacoes()

    assert ordem == ["commit", "publish"]


async def test_publicacao_nao_bloqueia_a_resposta_do_post(service, mock_estoque_ok, mock_sqs):
    """Best-effort de verdade: a resposta não espera o broker.

    Com a fila inalcançável, aguardar o boto3 adicionava ~7s a cada POST (medido
    contra o servico-estoque real) — acima do timeout dos clientes.
    """

    def _publish_lento(**kwargs) -> bool:
        time.sleep(2.0)
        return True

    mock_sqs.publish_pedido_criado = MagicMock(side_effect=_publish_lento)

    inicio = time.perf_counter()
    await service.criar_pedido(vendedor_id=uuid.uuid4(), payload=_dto((uuid.uuid4(), 1, "5.00")))
    duracao = time.perf_counter() - inicio

    assert duracao < 1.0, f"o POST esperou o broker por {duracao:.2f}s"
    await aguardar_publicacoes()
    mock_sqs.publish_pedido_criado.assert_called_once()


async def test_falha_de_publicacao_nao_afeta_o_pedido(service, mock_estoque_ok, mock_sqs):
    """Falha do SQS não propaga nem marca o pedido como não criado."""

    def _publish_que_falha(**kwargs) -> bool:
        raise RuntimeError("broker fora do ar")

    mock_sqs.publish_pedido_criado = MagicMock(side_effect=_publish_que_falha)

    pedido = await service.criar_pedido(
        vendedor_id=uuid.uuid4(), payload=_dto((uuid.uuid4(), 1, "5.00"))
    )

    await aguardar_publicacoes()
    assert pedido.status == StatusPedido.CONFIRMADO


# ---------------------------------------------------------------------------
# Compensação de estoque
# ---------------------------------------------------------------------------
async def test_compensacao_quando_segundo_item_falha_na_reserva(
    service, db_session, mock_estoque_ok, mock_sqs
):
    """1º item reservado, 2º sem estoque → libera o 1º, não persiste e não publica."""
    item_1, item_2 = uuid.uuid4(), uuid.uuid4()
    mock_estoque_ok.check_and_reserve = AsyncMock(
        side_effect=[
            ReservaResult(sucesso=True, status=StatusReserva.CONFIRMADO, mensagem="OK"),
            ReservaResult(
                sucesso=False, status=StatusReserva.ESTOQUE_INSUFICIENTE, mensagem="Sem estoque"
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc_info:
        await service.criar_pedido(
            vendedor_id=uuid.uuid4(), payload=_dto((item_1, 2, "10.00"), (item_2, 5, "20.00"))
        )

    assert exc_info.value.status_code == 422
    mock_estoque_ok.release_reserva.assert_awaited_once()
    assert mock_estoque_ok.release_reserva.call_args.kwargs["item_id"] == item_1
    mock_sqs.publish_pedido_criado.assert_not_called()
    assert (await db_session.scalars(select(Pedido))).all() == []


async def test_compensacao_quando_a_persistencia_falha(
    service, db_session, mock_estoque_ok, mock_sqs, monkeypatch
):
    """B6: se o commit falhar, o estoque reservado é devolvido e nada é publicado."""
    item_1 = uuid.uuid4()

    async def _commit_que_falha() -> None:
        raise SQLAlchemyError("falha simulada no commit")

    monkeypatch.setattr(db_session, "commit", _commit_que_falha)

    with pytest.raises(HTTPException) as exc_info:
        await service.criar_pedido(vendedor_id=uuid.uuid4(), payload=_dto((item_1, 1, "10.00")))

    assert exc_info.value.status_code == 503
    mock_estoque_ok.release_reserva.assert_awaited_once()
    assert mock_estoque_ok.release_reserva.call_args.kwargs["item_id"] == item_1
    mock_sqs.publish_pedido_criado.assert_not_called()


async def test_erro_inesperado_no_estoque_compensa_e_retorna_503(service, mock_estoque_ok, mock_sqs):
    mock_estoque_ok.check_and_reserve = AsyncMock(side_effect=RuntimeError("canal morto"))

    with pytest.raises(HTTPException) as exc_info:
        await service.criar_pedido(
            vendedor_id=uuid.uuid4(), payload=_dto((uuid.uuid4(), 1, "10.00"))
        )

    assert exc_info.value.status_code == 503
    mock_sqs.publish_pedido_criado.assert_not_called()


# ---------------------------------------------------------------------------
# Idempotência (header Idempotency-Key)
# ---------------------------------------------------------------------------
async def test_idempotency_key_reapresentada_devolve_o_mesmo_pedido(
    service, mock_estoque_ok, mock_sqs
):
    vendedor_id = uuid.uuid4()
    payload = _dto((uuid.uuid4(), 1, "10.00"))
    chave = "cliente-42-pedido-1"

    primeira = await service.criar_pedido(
        vendedor_id=vendedor_id, payload=payload, idempotency_key=chave
    )
    segunda = await service.criar_pedido(
        vendedor_id=vendedor_id, payload=payload, idempotency_key=chave
    )

    assert primeira.id == segunda.id == pedido_id_idempotente(chave)
    await aguardar_publicacoes()
    # A segunda chamada não reserva estoque de novo nem publica outro evento
    assert mock_estoque_ok.check_and_reserve.await_count == 1
    mock_sqs.publish_pedido_criado.assert_called_once()


async def test_idempotency_key_de_outro_vendedor_retorna_409(service):
    payload = _dto((uuid.uuid4(), 1, "10.00"))
    chave = "chave-concorrente"

    await service.criar_pedido(vendedor_id=uuid.uuid4(), payload=payload, idempotency_key=chave)

    with pytest.raises(HTTPException) as exc_info:
        await service.criar_pedido(vendedor_id=uuid.uuid4(), payload=payload, idempotency_key=chave)

    assert exc_info.value.status_code == 409


def test_pedido_id_idempotente_e_estavel_e_unico_por_chave():
    assert pedido_id_idempotente("abc") == pedido_id_idempotente("abc")
    assert pedido_id_idempotente("abc") != pedido_id_idempotente("abd")


# ---------------------------------------------------------------------------
# Cancelamento
# ---------------------------------------------------------------------------
async def test_cancelar_pedido_libera_estoque_e_muda_status(service, db_session, mock_estoque_ok):
    vendedor_id = uuid.uuid4()
    pedido = await _criar_pedido_persistido(db_session, vendedor_id, uuid.uuid4())

    cancelado = await service.cancelar_pedido(pedido_id=pedido.id, vendedor_id=vendedor_id)

    assert cancelado.status == StatusPedido.CANCELADO
    mock_estoque_ok.release_reserva.assert_awaited_once()


async def test_cancelar_pedido_ja_cancelado_lanca_conflito(service, db_session):
    vendedor_id = uuid.uuid4()
    pedido = await _criar_pedido_persistido(db_session, vendedor_id, uuid.uuid4())
    await service.cancelar_pedido(pedido_id=pedido.id, vendedor_id=vendedor_id)

    with pytest.raises(HTTPException) as exc_info:
        await service.cancelar_pedido(pedido_id=pedido.id, vendedor_id=vendedor_id)

    assert exc_info.value.status_code == 409


async def test_cancelar_pedido_de_outro_vendedor_lanca_403(service, db_session):
    pedido = await _criar_pedido_persistido(db_session, uuid.uuid4(), uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        await service.cancelar_pedido(pedido_id=pedido.id, vendedor_id=uuid.uuid4())

    assert exc_info.value.status_code == 403


async def test_cancelar_pedido_inexistente_lanca_404(service):
    with pytest.raises(HTTPException) as exc_info:
        await service.cancelar_pedido(pedido_id=uuid.uuid4(), vendedor_id=uuid.uuid4())

    assert exc_info.value.status_code == 404
