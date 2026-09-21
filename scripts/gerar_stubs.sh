#!/usr/bin/env bash
# =============================================================================
# Gera os stubs gRPC do contrato compartilhado (shared-protos/estoque.proto).
# Os arquivos gerados (estoque_pb2.py, estoque_pb2_grpc.py) NÃO são versionados
# (ver .gitignore) — são gerados no build da imagem e aqui, para desenvolvimento.
#
# Uso:  scripts/gerar_stubs.sh
# Requisito: grpcio-tools instalado
#            (pip install -r servico-estoque/requirements-dev.txt)
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRATO_DIR="$RAIZ/shared-protos"
CONTRATO="$CONTRATO_DIR/estoque.proto"
DESTINO="$RAIZ/servico-estoque"

# Interpretador: use PYTHON=/caminho/do/python se o `python` do PATH não tiver
# o grpcio-tools instalado (ex.: outro venv ativo). Ex.:
#   PYTHON=.venv/Scripts/python.exe scripts/gerar_stubs.sh
PYTHON_BIN="${PYTHON:-python}"

# O protoc é um binário nativo: no git-bash/MSYS ele não entende caminhos no
# estilo POSIX (/c/...) e falha com "directory does not exist". Converte para
# caminho nativo do Windows quando o cygpath existir (Linux/macOS não muda).
caminho_nativo() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

cd "$DESTINO"
"$PYTHON_BIN" -m grpc_tools.protoc \
    -I"$(caminho_nativo "$CONTRATO_DIR")" \
    --python_out=. \
    --grpc_python_out=. \
    "$(caminho_nativo "$CONTRATO")"

echo "Stubs gerados em $DESTINO:"
ls -1 "$DESTINO"/estoque_pb2*.py
