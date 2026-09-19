"""Carregamento dos stubs gRPC gerados a partir de `shared-protos/estoque.proto`.

Os stubs (`estoque_pb2.py`, `estoque_pb2_grpc.py`) são gerados — nunca
versionados (ver `.gitignore`). Gere-os com:

    scripts/gerar_stubs.sh              # desenvolvimento
    docker build ... (Dockerfile)       # a imagem gera no build
"""

from __future__ import annotations

from types import ModuleType

MENSAGEM_AJUDA = (
    "Stubs gRPC não encontrados (estoque_pb2 / estoque_pb2_grpc). "
    "Gere-os com: scripts/gerar_stubs.sh  — ou "
    "python -m grpc_tools.protoc -Ishared-protos --python_out=. "
    "--grpc_python_out=. shared-protos/estoque.proto"
)


def carregar_stubs() -> tuple[ModuleType, ModuleType]:
    """Devolve `(estoque_pb2, estoque_pb2_grpc)` ou falha com instrução clara."""
    try:
        import estoque_pb2  # type: ignore[import-not-found]
        import estoque_pb2_grpc  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - caminho de erro de setup
        raise RuntimeError(MENSAGEM_AJUDA) from exc
    return estoque_pb2, estoque_pb2_grpc


def stubs_disponiveis() -> bool:
    """Indica se os stubs já foram gerados (usado pelos testes)."""
    try:
        carregar_stubs()
    except RuntimeError:
        return False
    return True
