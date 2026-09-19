"""Teste ponta a ponta do servidor gRPC: sobe o servidor real e chama pelo contrato.

Prova que `shared-protos/estoque.proto` é efetivamente servido pelo
`servico-estoque` — incluindo o mapeamento de status do contrato e os códigos
de erro gRPC. Requer os stubs gerados (`scripts/gerar_stubs.sh`); sem eles o
módulo é pulado.
"""

from __future__ import annotations

import uuid

import grpc
import pytest
import pytest_asyncio

from app.grpc_server.server import montar_servidor_grpc

estoque_pb2 = pytest.importorskip("estoque_pb2", reason="stubs gRPC não gerados")
estoque_pb2_grpc = pytest.importorskip("estoque_pb2_grpc", reason="stubs gRPC não gerados")


@pytest_asyncio.fixture
async def servidor_grpc(session_factory, lock_manager, publisher):
    estoque_grpc = montar_servidor_grpc(
        session_factory=session_factory,
        lock_manager=lock_manager,
        publisher=publisher,
        host="127.0.0.1",
        port=0,  # porta efêmera — evita conflito entre execuções
    )
    await estoque_grpc.servidor.start()
    yield estoque_grpc
    await estoque_grpc.servidor.stop(grace=None)


@pytest_asyncio.fixture
async def stub(servidor_grpc):
    async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor_grpc.porta}") as canal:
        yield estoque_pb2_grpc.EstoqueServiceStub(canal)


async def test_check_and_reserve_pelo_contrato(session, stub, item_factory):
    item = await item_factory(quantidade=5)

    resposta = await stub.CheckAndReserve(
        estoque_pb2.ReservaRequest(
            item_id=str(item.id),
            quantidade=2,
            pedido_id=str(uuid.uuid4()),
            request_uuid=str(uuid.uuid4()),
        )
    )

    assert resposta.sucesso is True
    assert resposta.status == "CONFIRMADO"
    await session.refresh(item)
    assert item.quantidade_disponivel == 3
    assert item.quantidade_reservada == 2


async def test_reserva_sem_saldo_pelo_contrato(session, stub, item_factory):
    item = await item_factory(quantidade=1)

    resposta = await stub.CheckAndReserve(
        estoque_pb2.ReservaRequest(
            item_id=str(item.id),
            quantidade=3,
            pedido_id=str(uuid.uuid4()),
            request_uuid=str(uuid.uuid4()),
        )
    )

    assert resposta.sucesso is False
    assert resposta.status == "ESTOQUE_INSUFICIENTE"


async def test_item_inexistente_retorna_not_found(stub):
    with pytest.raises(grpc.aio.AioRpcError) as erro:
        await stub.CheckAndReserve(
            estoque_pb2.ReservaRequest(
                item_id=str(uuid.uuid4()),
                quantidade=1,
                pedido_id=str(uuid.uuid4()),
                request_uuid=str(uuid.uuid4()),
            )
        )
    assert erro.value.code() is grpc.StatusCode.NOT_FOUND


async def test_quantidade_invalida_retorna_invalid_argument(stub):
    with pytest.raises(grpc.aio.AioRpcError) as erro:
        await stub.CheckAndReserve(
            estoque_pb2.ReservaRequest(
                item_id=str(uuid.uuid4()),
                quantidade=0,
                pedido_id=str(uuid.uuid4()),
                request_uuid=str(uuid.uuid4()),
            )
        )
    assert erro.value.code() is grpc.StatusCode.INVALID_ARGUMENT


async def test_pedido_id_invalido_retorna_invalid_argument(stub):
    with pytest.raises(grpc.aio.AioRpcError) as erro:
        await stub.CheckAndReserve(
            estoque_pb2.ReservaRequest(
                item_id=str(uuid.uuid4()),
                quantidade=1,
                pedido_id="nao-e-uuid",
                request_uuid=str(uuid.uuid4()),
            )
        )
    assert erro.value.code() is grpc.StatusCode.INVALID_ARGUMENT


async def test_consultar_item_pelo_contrato(stub, item_factory):
    item = await item_factory(quantidade=9, nome="Água")

    resposta = await stub.ConsultarItem(estoque_pb2.ItemRequest(item_id=str(item.id)))

    assert resposta.item_id == str(item.id)
    assert resposta.nome == "Água"
    assert resposta.quantidade_disponivel == 9
    assert resposta.quantidade_reservada == 0


async def test_consultar_item_inexistente_retorna_not_found(stub):
    with pytest.raises(grpc.aio.AioRpcError) as erro:
        await stub.ConsultarItem(estoque_pb2.ItemRequest(item_id=str(uuid.uuid4())))
    assert erro.value.code() is grpc.StatusCode.NOT_FOUND


async def test_release_reserva_pelo_contrato(session, stub, item_factory):
    item = await item_factory(quantidade=4)
    pedido_id = uuid.uuid4()

    await stub.CheckAndReserve(
        estoque_pb2.ReservaRequest(
            item_id=str(item.id),
            quantidade=4,
            pedido_id=str(pedido_id),
            request_uuid=str(uuid.uuid4()),
        )
    )
    resposta = await stub.ReleaseReserva(
        estoque_pb2.ReleaseRequest(
            item_id=str(item.id), quantidade=4, pedido_id=str(pedido_id)
        )
    )

    assert resposta.sucesso is True
    await session.refresh(item)
    assert item.quantidade_disponivel == 4
    assert item.quantidade_reservada == 0


async def test_release_sem_reserva_retorna_sucesso_falso(stub, item_factory):
    item = await item_factory(quantidade=4)

    resposta = await stub.ReleaseReserva(
        estoque_pb2.ReleaseRequest(
            item_id=str(item.id), quantidade=4, pedido_id=str(uuid.uuid4())
        )
    )

    assert resposta.sucesso is False
    assert "reserva" in resposta.mensagem.lower()
