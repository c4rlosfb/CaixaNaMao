"""Testes unitários e de integração de telemetria e rastreio UUID (servico-estoque).

Garante que o padrão de identificação UUID (Issue #10 / Sprint 3) funcione em todos
os níveis do servico-estoque:
1. Validação e geração de UUIDs (RFC 4122);
2. Escopo assíncrono via contextvars;
3. Interceptador gRPC com captura de metadados e resposta via trailing metadata;
4. Injeção no formato de logging com CorrelationLogFilter;
5. Middleware ASGI no endpoint HTTP de health check;
6. Propagação de correlation_id e request_id em eventos SQS (EstoqueAtualizado).
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock

import grpc
import pytest
from httpx import ASGITransport, AsyncClient

from app.clients.event_publisher import SQSEventPublisher, novo_evento_estoque_atualizado
from app.grpc_server.server import montar_servidor_grpc
from app.health import criar_app_health
from app.telemetry import (
    CORRELATION_ID_HEADER,
    CORRELATION_ID_HTTP_HEADER,
    REQUEST_ID_HEADER,
    CorrelationLogFilter,
    correlation_scope,
    get_correlation_id,
    get_request_id,
    validar_ou_gerar_uuid,
    validar_uuid,
)

estoque_pb2 = pytest.importorskip("estoque_pb2", reason="stubs gRPC não gerados")
estoque_pb2_grpc = pytest.importorskip("estoque_pb2_grpc", reason="stubs gRPC não gerados")


def test_validar_uuid_com_valores_validos() -> None:
    uuid_str = str(uuid.uuid4())
    assert validar_uuid(uuid_str) == uuid_str
    assert validar_uuid(uuid_str.upper()) == uuid_str
    assert validar_uuid(f"  {uuid_str}  ") == uuid_str


def test_validar_uuid_com_valores_invalidos() -> None:
    assert validar_uuid(None) is None
    assert validar_uuid("") is None
    assert validar_uuid("invalido") is None
    assert validar_uuid("12345") is None


def test_validar_ou_gerar_uuid() -> None:
    uuid_existente = str(uuid.uuid4())
    assert validar_ou_gerar_uuid(uuid_existente) == uuid_existente

    gerado = validar_ou_gerar_uuid(None)
    assert uuid.UUID(gerado).version == 4


def test_correlation_scope_define_e_restaura_contexto() -> None:
    assert get_correlation_id() is None
    assert get_request_id() is None

    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())

    with correlation_scope(correlation_id=cid, request_id=rid) as (c, r):
        assert c == cid
        assert r == rid
        assert get_correlation_id() == cid
        assert get_request_id() == rid

    assert get_correlation_id() is None
    assert get_request_id() is None


def test_correlation_log_filter_injeta_atributos_no_record() -> None:
    filtro = CorrelationLogFilter()
    registro = logging.LogRecord(
        name="estoque",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="mensagem",
        args=(),
        exc_info=None,
    )

    filtro.filter(registro)
    assert registro.correlation_id == "-"
    assert registro.request_id == "-"

    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())
    with correlation_scope(correlation_id=cid, request_id=rid):
        filtro.filter(registro)
        assert registro.correlation_id == cid
        assert registro.request_id == rid


@pytest.mark.asyncio
async def test_grpc_interceptor_preserva_metadados_e_devolve_trailing(
    session_factory, lock_manager, publisher, item_factory
) -> None:
    """Verifica que o interceptor gRPC captura x-correlation-id e devolve no trailing metadata."""
    servidor = montar_servidor_grpc(
        session_factory=session_factory,
        lock_manager=lock_manager,
        publisher=publisher,
        host="127.0.0.1",
        port=0,
    )
    await servidor.servidor.start()

    item = await item_factory(quantidade=10)
    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())

    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor.porta}") as canal:
            stub = estoque_pb2_grpc.EstoqueServiceStub(canal)
            chamada = stub.CheckAndReserve(
                estoque_pb2.ReservaRequest(
                    item_id=str(item.id),
                    quantidade=1,
                    pedido_id=str(uuid.uuid4()),
                    request_uuid=rid,
                ),
                metadata=(
                    (CORRELATION_ID_HEADER, cid),
                    (REQUEST_ID_HEADER, rid),
                ),
            )
            resposta = await chamada
            assert resposta.sucesso is True

            trailing = dict(list(await chamada.trailing_metadata()))
            assert trailing.get(CORRELATION_ID_HEADER) == cid
            assert trailing.get(REQUEST_ID_HEADER) == rid
    finally:
        await servidor.servidor.stop(grace=None)


@pytest.mark.asyncio
async def test_grpc_interceptor_gera_uuid_quando_metadados_ausentes(
    session_factory, lock_manager, publisher, item_factory
) -> None:
    """Sem metadados, o interceptor gera novos UUIDs válidos e os devolve no trailing."""
    servidor = montar_servidor_grpc(
        session_factory=session_factory,
        lock_manager=lock_manager,
        publisher=publisher,
        host="127.0.0.1",
        port=0,
    )
    await servidor.servidor.start()

    item = await item_factory(quantidade=10)

    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{servidor.porta}") as canal:
            stub = estoque_pb2_grpc.EstoqueServiceStub(canal)
            chamada = stub.CheckAndReserve(
                estoque_pb2.ReservaRequest(
                    item_id=str(item.id),
                    quantidade=1,
                    pedido_id=str(uuid.uuid4()),
                    request_uuid="",
                )
            )
            resposta = await chamada
            assert resposta.sucesso is True

            trailing = dict(list(await chamada.trailing_metadata()))
            corr_resp = trailing.get(CORRELATION_ID_HEADER)
            req_resp = trailing.get(REQUEST_ID_HEADER)
            assert corr_resp is not None
            assert req_resp is not None
            assert uuid.UUID(corr_resp).version == 4
            assert uuid.UUID(req_resp).version == 4
    finally:
        await servidor.servidor.stop(grace=None)


@pytest.mark.asyncio
async def test_health_app_middleware_injeta_headers(session_factory, redis_fake) -> None:
    app_health = criar_app_health(session_factory=session_factory, redis_client=redis_fake)
    transport = ASGITransport(app=app_health)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Sem header prévio
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert "x-correlation-id" in resp.headers
        assert "x-request-id" in resp.headers
        assert uuid.UUID(resp.headers["x-correlation-id"]).version == 4

        # Com header prévio
        cid = str(uuid.uuid4())
        resp_cid = await client.get("/health", headers={CORRELATION_ID_HTTP_HEADER: cid})
        assert resp_cid.status_code == 200
        assert resp_cid.headers["x-correlation-id"] == cid


def test_evento_estoque_atualizado_inclui_correlation_e_message_attributes() -> None:
    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())
    item_id = uuid.uuid4()
    pedido_id = uuid.uuid4()

    evento = novo_evento_estoque_atualizado(
        tipo_movimentacao="RESERVA",
        item_id=item_id,
        pedido_id=pedido_id,
        quantidade=2,
        quantidade_disponivel=8,
        quantidade_reservada=2,
        correlation_id=cid,
        request_id=rid,
    )

    assert evento["correlation_id"] == cid
    assert evento["request_id"] == rid
    assert evento["evento"] == "EstoqueAtualizado"

    # Testa publicação com MessageAttributes
    publisher = SQSEventPublisher(queue_name="teste-fila")
    mock_sqs = MagicMock()
    publisher._cliente = mock_sqs
    publisher._url_fila = "https://sqs.us-east-1.amazonaws.com/123456789012/teste-fila"

    publisher._enviar_bloqueante(evento)
    mock_sqs.send_message.assert_called_once()
    call_kwargs = mock_sqs.send_message.call_args.kwargs
    attrs = call_kwargs["MessageAttributes"]
    assert attrs["correlation_id"]["StringValue"] == cid
    assert attrs["request_id"]["StringValue"] == rid
