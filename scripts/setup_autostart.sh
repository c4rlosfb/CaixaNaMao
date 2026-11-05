#!/bin/bash
set -euo pipefail

SERVICE_NAME="caixanamao.service"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Configurando inicialização automática do CaixaNaMão no boot via systemd..."

if [ ! -f "${SCRIPT_DIR}/${SERVICE_NAME}" ]; then
    echo "ERRO: Arquivo ${SCRIPT_DIR}/${SERVICE_NAME} não encontrado."
    exit 1
fi

sudo cp "${SCRIPT_DIR}/${SERVICE_NAME}" "${SERVICE_DEST}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "==> Serviço ${SERVICE_NAME} habilitado com sucesso!"
echo "==> Os containers do Docker iniciarão automaticamente ao ligar ou reiniciar a instância EC2."
