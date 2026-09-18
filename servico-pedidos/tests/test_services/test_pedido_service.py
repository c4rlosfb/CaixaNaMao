"""Testes unitários do PedidoService com foco na lógica de negócio e compensação."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.clients.estoque_client import ReleaseResult, ReservaResult, StatusReserva
from app.models.enums import StatusPedido
from app.models.pedido import ItemPedido, Pedido
from app.schemas.pedido import ItemCreate, PedidoCreate
from app.services.pedido_service import PedidoService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    return repo


@pytest.fixture
def mock_estoque() -> AsyncMock:
    estoque = AsyncMock()
    estoque.check_and_reserve = AsyncMock(
        return_value=ReservaResult(
            sucesso=True,
            status=StatusReserva.RESERVADO,
            mensagem="OK",
        )
    )
    estoque.release_reserva = AsyncMock(
        return_value=ReleaseResult(
            sucesso=True,
            mensagem="OK",
        )
    )
    return estoque


@pytest.fixture
def mock_sqs() -> AsyncMock:
    sqs = AsyncMock()
    sqs.publicar_pedido_criado = AsyncMock()
    return sqs


@pytest.fixture
def service(mock_repo: AsyncMock, mock_estoque: AsyncMock, mock_sqs: AsyncMock) -> PedidoService:
    return PedidoService(
        repository=mock_repo,
        estoque_client=mock_estoque,
        sqs_client=mock_sqs,
    )


async def test_criar_pedido_sucesso(
    service: PedidoService,
    mock_repo: AsyncMock,
    mock_estoque: AsyncMock,
    mock_sqs: AsyncMock,
) -> None:
    vendedor_id = uuid.uuid4()
    item_id = uuid.uuid4()
    dto = PedidoCreate(
        itens=[
            ItemCreate(
                item_id=item_id,
                quantidade=3,
                preco_unitario=Decimal("25.00"),
            )
        ]
    )

    pedido_mock = Pedido(
        id=uuid.uuid4(),
        vendedor_id=vendedor_id,
        status=StatusPedido.CONFIRMADO,
        total=Decimal("75.00"),
    )
    mock_repo.create.return_value = pedido_mock

    resultado = await service.criar_pedido(dto, vendedor_id)

    assert resultado == pedido_mock
    mock_estoque.check_and_reserve.assert_called_once()
    mock_repo.create.assert_called_once()
    mock_sqs.publicar_pedido_criado.assert_called_once()


async def test_compensacao_quando_segundo_item_falha_na_reserva(
    service: PedidoService,
    mock_repo: AsyncMock,
    mock_estoque: AsyncMock,
) -> None:
    """Verifica padrão de compensação: se o 2º item falhar, o 1º deve ser liberado (release_reserva)."""
    vendedor_id = uuid.uuid4()
    item1_id = uuid.uuid4()
    item2_id = uuid.uuid4()

    dto = PedidoCreate(
        itens=[
            ItemCreate(item_id=item1_id, quantidade=2, preco_unitario=Decimal("10.00")),
            ItemCreate(item_id=item2_id, quantidade=5, preco_unitario=Decimal("20.00")),
        ]
    )

    # 1º item sucesso, 2º item sem estoque
    mock_estoque.check_and_reserve.side_effect = [
        ReservaResult(sucesso=True, status=StatusReserva.RESERVADO, mensagem="OK"),
        ReservaResult(sucesso=False, status=StatusReserva.ESTOQUE_INSUFICIENTE, mensagem="Sem estoque"),
    ]

    with pytest.raises(HTTPException) as exc_info:
        await service.criar_pedido(dto, vendedor_id)

    assert exc_info.value.status_code == 422
    assert "Sem estoque" in exc_info.value.detail

    # Verifica se chamou compensação para o item1
    mock_estoque.release_reserva.assert_called_once()
    call_args = mock_estoque.release_reserva.call_args
    assert call_args[0][0] == item1_id
    assert call_args[0][1] == 2

    # Banco NÃO deve ser chamado
    mock_repo.create.assert_not_called()


async def test_cancelar_pedido_sucesso(
    service: PedidoService,
    mock_repo: AsyncMock,
    mock_estoque: AsyncMock,
) -> None:
    vendedor_id = uuid.uuid4()
    pedido_id = uuid.uuid4()
    item_id = uuid.uuid4()

    pedido_existente = Pedido(
        id=pedido_id,
        vendedor_id=vendedor_id,
        status=StatusPedido.CONFIRMADO,
        total=Decimal("50.00"),
        itens=[
            ItemPedido(
                id=uuid.uuid4(),
                pedido_id=pedido_id,
                item_id=item_id,
                quantidade=2,
                preco_unitario=Decimal("25.00"),
            )
        ],
    )
    mock_repo.get_by_id.return_value = pedido_existente
    mock_repo.update_status.return_value = pedido_existente

    resultado = await service.cancelar_pedido(pedido_id, vendedor_id)

    # Verifica que release foi chamado com os dados do item
    mock_estoque.release_reserva.assert_called_once_with(item_id, 2)
    mock_repo.update_status.assert_called_once_with(pedido_id, StatusPedido.CANCELADO)
    assert resultado.status == StatusPedido.CANCELADO


async def test_cancelar_pedido_ja_cancelado_lanca_conflito(
    service: PedidoService,
    mock_repo: AsyncMock,
) -> None:
    vendedor_id = uuid.uuid4()
    pedido_id = uuid.uuid4()

    pedido_cancelado = Pedido(
        id=pedido_id,
        vendedor_id=vendedor_id,
        status=StatusPedido.CANCELADO,
        total=Decimal("50.00"),
        itens=[],
    )
    mock_repo.get_by_id.return_value = pedido_cancelado

    with pytest.raises(HTTPException) as exc_info:
        await service.cancelar_pedido(pedido_id, vendedor_id)

    assert exc_info.value.status_code == 409
    assert "já se encontra cancelado" in exc_info.value.detail


async def test_cancelar_pedido_outro_vendedor_lanca_403(
    service: PedidoService,
    mock_repo: AsyncMock,
) -> None:
    vendedor_id = uuid.uuid4()
    outro_vendedor_id = uuid.uuid4()
    pedido_id = uuid.uuid4()

    pedido = Pedido(
        id=pedido_id,
        vendedor_id=outro_vendedor_id,
        status=StatusPedido.CONFIRMADO,
        total=Decimal("50.00"),
        itens=[],
    )
    mock_repo.get_by_id.return_value = pedido

    with pytest.raises(HTTPException) as exc_info:
        await service.cancelar_pedido(pedido_id, vendedor_id)

    assert exc_info.value.status_code == 403
    assert "Sem permissão" in exc_info.value.detail
