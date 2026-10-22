"""Testes unitários e de integração do módulo de telemetria e rastreio UUID (servico-pedidos).

Garante que o padrão de identificação UUID (Issue #10 / Sprint 3) funcione em todos
os níveis:
1. Validação e geração de UUIDs (RFC 4122);
2. Escopo assíncrono via contextvars;
3. Middleware HTTP ASGI com headers X-Correlation-ID e X-Request-ID;
4. Injeção no formato de logging com CorrelationLogFilter;
5. Propagação de metadados para chamadas gRPC;
6. Injeção de correlation_id e request_id em eventos SQS.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.clients.sqs_client import SQSClient
from app.main import app
from app.telemetry import (
    CORRELATION_ID_HEADER,
    CORRELATION_ID_HTTP_HEADER,
    REQUEST_ID_HEADER,
    REQUEST_ID_HTTP_HEADER,
    CorrelationLogFilter,
    correlation_scope,
    get_correlation_id,
    get_request_id,
    obter_metadados_grpc,
    validar_ou_gerar_uuid,
    validar_uuid,
)


def test_validar_uuid_com_valores_validos() -> None:
    uuid_str = str(uuid.uuid4())
    assert validar_uuid(uuid_str) == uuid_str
    # Valida maiúsculas sendo normalizadas para minúsculas
    assert validar_uuid(uuid_str.upper()) == uuid_str
    # Espaços nas pontas
    assert validar_uuid(f"  {uuid_str}  ") == uuid_str


def test_validar_uuid_com_valores_invalidos() -> None:
    assert validar_uuid(None) is None
    assert validar_uuid("") is None
    assert validar_uuid("nao-e-um-uuid") is None
    assert validar_uuid("12345") is None
    assert validar_uuid("123e4567-e89b-12d3-a456-42661417400Z") is None


def test_validar_ou_gerar_uuid() -> None:
    uuid_existente = str(uuid.uuid4())
    # Mantém o existente se for válido
    assert validar_ou_gerar_uuid(uuid_existente) == uuid_existente

    # Gera novo válido se for inválido ou nulo
    gerado_1 = validar_ou_gerar_uuid(None)
    assert uuid.UUID(gerado_1).version == 4

    gerado_2 = validar_ou_gerar_uuid("invalido")
    assert uuid.UUID(gerado_2).version == 4
    assert gerado_2 != "invalido"


def test_correlation_scope_define_e_restaura_contexto() -> None:
    assert get_correlation_id() is None
    assert get_request_id() is None

    corr_externo = str(uuid.uuid4())
    req_externo = str(uuid.uuid4())

    with correlation_scope(correlation_id=corr_externo, request_id=req_externo) as (c1, r1):
        assert c1 == corr_externo
        assert r1 == req_externo
        assert get_correlation_id() == corr_externo
        assert get_request_id() == req_externo

        # Escopo aninhado (subtransação ou chamada interna)
        corr_interno = str(uuid.uuid4())
        req_interno = str(uuid.uuid4())
        with correlation_scope(correlation_id=corr_interno, request_id=req_interno) as (c2, r2):
            assert c2 == corr_interno
            assert r2 == req_interno
            assert get_correlation_id() == corr_interno
            assert get_request_id() == req_interno

        # Retorno ao escopo pai
        assert get_correlation_id() == corr_externo
        assert get_request_id() == req_externo

    assert get_correlation_id() is None
    assert get_request_id() is None


def test_correlation_log_filter_injeta_atributos_no_record() -> None:
    filtro = CorrelationLogFilter()
    registro = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1, msg="mensagem", args=(), exc_info=None
    )

    # Fora de escopo: preenche "-"
    filtro.filter(registro)
    assert registro.correlation_id == "-"
    assert registro.request_id == "-"

    # Dentro de escopo: preenche com os UUIDs do contexto
    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())
    with correlation_scope(correlation_id=cid, request_id=rid):
        filtro.filter(registro)
        assert registro.correlation_id == cid
        assert registro.request_id == rid


def test_obter_metadados_grpc_com_e_sem_contexto() -> None:
    # Sem contexto: gera UUIDs novos
    metas = obter_metadados_grpc()
    meta_dict = dict(metas)
    assert CORRELATION_ID_HEADER in meta_dict
    assert REQUEST_ID_HEADER in meta_dict
    assert uuid.UUID(meta_dict[CORRELATION_ID_HEADER]).version == 4
    assert uuid.UUID(meta_dict[REQUEST_ID_HEADER]).version == 4

    # Com contexto: preserva exatamente os UUIDs ativos
    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())
    with correlation_scope(correlation_id=cid, request_id=rid):
        metas_contexto = dict(obter_metadados_grpc())
        assert metas_contexto[CORRELATION_ID_HEADER] == cid
        assert metas_contexto[REQUEST_ID_HEADER] == rid


@pytest.mark.asyncio
async def test_middleware_injeta_headers_na_resposta_health() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert CORRELATION_ID_HTTP_HEADER.lower() in [k.lower() for k in response.headers.keys()]
        assert REQUEST_ID_HTTP_HEADER.lower() in [k.lower() for k in response.headers.keys()]

        corr_header = response.headers.get("x-correlation-id")
        req_header = response.headers.get("x-request-id")
        assert corr_header is not None
        assert req_header is not None
        assert uuid.UUID(corr_header).version == 4
        assert uuid.UUID(req_header).version == 4


@pytest.mark.asyncio
async def test_middleware_preserva_correlation_id_do_cliente() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        meu_corr_id = str(uuid.uuid4())
        response = await client.get(
            "/health",
            headers={"X-Correlation-ID": meu_corr_id},
        )
        assert response.status_code == 200
        assert response.headers.get("x-correlation-id") == meu_corr_id
        # X-Request-ID deve ser gerado separadamente
        assert response.headers.get("x-request-id") is not None
        assert response.headers.get("x-request-id") != meu_corr_id


@pytest.mark.asyncio
async def test_middleware_substitui_correlation_id_invalido() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"X-Correlation-ID": "nao-sou-um-uuid"},
        )
        assert response.status_code == 200
        corr_header = response.headers.get("x-correlation-id")
        assert corr_header != "nao-sou-um-uuid"
        assert uuid.UUID(corr_header).version == 4


def test_sqs_publica_correlation_id_e_request_id(monkeypatch) -> None:
    sqs_client = SQSClient()
    mock_boto = MagicMock()
    sqs_client._sqs = mock_boto
    sqs_client._queue_url = "https://sqs.us-east-1.amazonaws.com/123456789012/caixanamao-pedidos-criados"

    cid = str(uuid.uuid4())
    rid = str(uuid.uuid4())
    pedido_id = uuid.uuid4()
    vendedor_id = uuid.uuid4()

    sucesso = sqs_client.publish_pedido_criado(
        pedido_id=pedido_id,
        vendedor_id=vendedor_id,
        total="50.00",
        itens=[{"item_id": str(uuid.uuid4()), "quantidade": 1, "preco_unitario": "50.00"}],
        correlation_id=cid,
        request_id=rid,
    )
    assert sucesso is True
    mock_boto.send_message.assert_called_once()
    call_kwargs = mock_boto.send_message.call_args.kwargs

    # Verifica MessageAttributes
    msg_attrs = call_kwargs["MessageAttributes"]
    assert msg_attrs["correlation_id"]["StringValue"] == cid
    assert msg_attrs["request_id"]["StringValue"] == rid

    # Verifica Body JSON
    import json

    body = json.loads(call_kwargs["MessageBody"])
    assert body["correlation_id"] == cid
    assert body["request_id"] == rid
    assert body["payload"]["pedido_id"] == str(pedido_id)
