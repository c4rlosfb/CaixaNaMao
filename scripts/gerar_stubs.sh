#!/usr/bin/env bash
# =============================================================================
# Gera os stubs gRPC do contrato compartilhado (shared-protos/estoque.proto).
# Os arquivos gerados (estoque_pb2.py, estoque_pb2_grpc.py) NÃO são versionados
# (ver .gitignore) — são gerados no build da imagem e aqui, para desenvolvimento.
#
# Uso:  scripts/gerar_stubs.sh
# Requisito: grpcio-tools instalado (pip install -r servico-estoque/requirements.txt)
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRATO_ORIGEM="$RAIZ/shared-protos"
DESTINO="$RAIZ/servico-estoque"

cd "$DESTINO"
python -m grpc_tools.protoc \
    -I"$CONTRATO_ORIGEM" \
    --python_out=. \
    --grpc_python_out=. \
    "$CONTRATO_ORIGEM/estoque.proto"

echo "Stubs gerados em $DESTINO:"
ls -1 "$DESTINO"/estoque_pb2*.py
