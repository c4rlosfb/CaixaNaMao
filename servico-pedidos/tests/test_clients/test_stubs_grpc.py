"""Regressão do caminho gRPC real: os stubs gerados precisam ser importáveis.

O `protoc` emite `import estoque_pb2` (absoluto) dentro de `estoque_pb2_grpc.py`.
Importado como `app.clients.estoque_pb2_grpc`, esse import não resolve — e o
`EstoqueClient` fica com `disponivel == False`, derrubando TODO o caminho de
reserva de estoque (falha silenciosa: o serviço responde 503).

Esse bug passou pelos testes com mock e só apareceu na verificação ponta a ponta
contra o servico-estoque real (review do PR #26).
"""

from __future__ import annotations

import pytest

pytest.importorskip("app.clients.estoque_pb2", reason="stubs gRPC não gerados neste ambiente")


def test_stubs_do_contrato_sao_importaveis() -> None:
    from app.clients import estoque_pb2, estoque_pb2_grpc

    assert estoque_pb2.ReservaRequest is not None
    assert estoque_pb2_grpc.EstoqueServiceStub is not None


def test_cliente_de_producao_carrega_os_stubs() -> None:
    """Com os stubs no lugar, o cliente real precisa estar disponível."""
    from app.clients.estoque_client import EstoqueClient

    cliente = EstoqueClient()

    assert cliente.disponivel is True


def test_cliente_real_usa_o_alvo_configurado() -> None:
    from app.clients.estoque_client import EstoqueClient
    from app.config import settings

    cliente = EstoqueClient()

    assert cliente.disponivel is True
    assert settings.estoque_grpc_port > 0
