"""Guarda de deriva do contrato `shared-protos/estoque.proto`.

Este arquivo é a fonte da verdade da comunicação gRPC Pedidos <-> Estoque. A suíte
compila o contrato e confere a estrutura do descritor gerado: serviço, métodos,
tipos de entrada/saída, e nome/tipo/número de cada campo das mensagens.

Por que fixar nome **e número**: renumerar um campo não quebra a compilação, mas
quebra a compatibilidade de fio com qualquer serviço já implantado (o número é a
identidade do campo no protocolo binário). Se o contrato precisar mudar de
propósito, o teste muda junto — o objetivo é que a mudança seja uma decisão
consciente e revisável, não um acidente.

Não depende da stack estar no ar: só do `grpcio-tools`/`protobuf`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CONTRATO = RAIZ / "shared-protos" / "estoque.proto"

# Estrutura esperada do contrato (issue #7 + docs/arquitetura.md §3.3)
RPC_ESPERADAS = {
    "CheckAndReserve": ("ReservaRequest", "ReservaResponse"),
    "ReleaseReserva": ("ReleaseRequest", "ReleaseResponse"),
    "ConsultarItem": ("ItemRequest", "ItemResponse"),
}

CAMPOS_ESPERADOS: dict[str, dict[str, tuple[int, str]]] = {
    "ReservaRequest": {
        "item_id": (1, "TYPE_STRING"),
        "quantidade": (2, "TYPE_INT32"),
        "pedido_id": (3, "TYPE_STRING"),
        "request_uuid": (4, "TYPE_STRING"),
    },
    "ReservaResponse": {
        "sucesso": (1, "TYPE_BOOL"),
        "status": (2, "TYPE_STRING"),
        "mensagem": (3, "TYPE_STRING"),
    },
    "ReleaseRequest": {
        "item_id": (1, "TYPE_STRING"),
        "quantidade": (2, "TYPE_INT32"),
        "pedido_id": (3, "TYPE_STRING"),
    },
    "ReleaseResponse": {
        "sucesso": (1, "TYPE_BOOL"),
        "mensagem": (2, "TYPE_STRING"),
    },
    "ItemRequest": {
        "item_id": (1, "TYPE_STRING"),
    },
    "ItemResponse": {
        "item_id": (1, "TYPE_STRING"),
        "nome": (2, "TYPE_STRING"),
        "quantidade_disponivel": (3, "TYPE_INT32"),
        "quantidade_reservada": (4, "TYPE_INT32"),
    },
}


@pytest.fixture(scope="module")
def descritor():
    """Descritor do arquivo .proto, obtido compilando o contrato com o protoc."""
    try:
        import grpc_tools.protoc  # noqa: F401
        from google.protobuf import descriptor_pb2
    except ImportError:  # pragma: no cover - caminho de ambiente incompleto
        pytest.skip(
            "grpcio-tools/protobuf não instalados: "
            "pip install -r tests/integracao/requirements.txt"
        )

    destino = Path(tempfile.mkdtemp(prefix="cnm-descriptor-"))
    conjunto = destino / "contrato.desc"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{CONTRATO.parent}",
            f"--descriptor_set_out={conjunto}",
            str(CONTRATO),
        ],
        check=True,
        capture_output=True,
    )

    arquivos = descriptor_pb2.FileDescriptorSet()
    arquivos.ParseFromString(conjunto.read_bytes())
    assert arquivos.file, "o protoc não gerou descritor para o contrato"

    return arquivos.file[0]


def test_contrato_existe_e_compila() -> None:
    assert CONTRATO.is_file(), f"contrato ausente em {CONTRATO}"


def test_pacote_e_sintaxe(descritor) -> None:
    assert descritor.syntax == "proto3"
    assert descritor.package == "estoque"


def test_servico_e_metodos(descritor) -> None:
    servicos = {servico.name: servico for servico in descritor.service}
    assert "EstoqueService" in servicos, "o contrato precisa expor EstoqueService"

    metodos = {metodo.name: metodo for metodo in servicos["EstoqueService"].method}
    assert set(metodos) == set(RPC_ESPERADAS), (
        f"RPCs do contrato divergem do esperado: {sorted(metodos)}"
    )

    for nome, (entrada, saida) in RPC_ESPERADAS.items():
        metodo = metodos[nome]
        assert metodo.input_type == f".estoque.{entrada}"
        assert metodo.output_type == f".estoque.{saida}"
        # Unary: a integração atual não usa streaming em nenhum sentido.
        assert metodo.client_streaming is False
        assert metodo.server_streaming is False


def _tipo_numerico(nome: str) -> int:
    """Converte 'TYPE_STRING' no número correspondente do FieldDescriptorProto."""
    from google.protobuf.descriptor_pb2 import FieldDescriptorProto

    return getattr(FieldDescriptorProto.Type, nome)


def test_campos_das_mensagens(descritor) -> None:
    """Nome, número e tipo de cada campo — o número é a identidade no fio."""
    mensagens = {mensagem.name: mensagem for mensagem in descritor.message_type}
    assert set(CAMPOS_ESPERADOS) <= set(mensagens), (
        f"mensagens ausentes no contrato: {sorted(set(CAMPOS_ESPERADOS) - set(mensagens))}"
    )

    for nome_mensagem, campos in CAMPOS_ESPERADOS.items():
        reais = {campo.name: (campo.number, campo.type) for campo in mensagens[nome_mensagem].field}
        for nome_campo, (numero, tipo) in campos.items():
            assert nome_campo in reais, f"{nome_mensagem}.{nome_campo} desapareceu do contrato"
            numero_real, tipo_real = reais[nome_campo]
            assert numero_real == numero, (
                f"{nome_mensagem}.{nome_campo} mudou de número ({numero} -> {numero_real}): "
                "isso quebra a compatibilidade de fio"
            )
            assert tipo_real == _tipo_numerico(tipo), (
                f"{nome_mensagem}.{nome_campo} mudou de tipo ({tipo})"
            )


def test_status_documentados_no_comentario_do_contrato() -> None:
    """Os status de `ReservaResponse` são parte do contrato com o servico-pedidos."""
    texto = CONTRATO.read_text(encoding="utf-8")
    for status in ("CONFIRMADO", "ESTOQUE_INSUFICIENTE", "ESTOQUE_BLOQUEADO"):
        assert status in texto, f"status {status} não está documentado no contrato"
