#!/usr/bin/env bash
# =============================================================================
# CaixaNaMão • Script de Reset Rápido do Estoque (Demonstração da Sprint 3)
#
# Uso dentro da EC2:
#   bash scripts/reset_estoque.sh
# =============================================================================

set -e

echo "=========================================================="
echo "  🔄 CaixaNaMão — Restaurando Estoque para a Demonstração"
echo "=========================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

echo ">> Executando seed_dev.sql no container PostgreSQL (estoque_db)..."
docker compose exec -T postgres psql -U postgres -d estoque_db < "${SCRIPT_DIR}/seed_dev.sql"

echo ""
echo ">> Saldos atualizados no banco de dados:"
docker compose exec -T postgres psql -U postgres -d estoque_db -c \
  "SELECT nome, quantidade_disponivel, preco FROM itens ORDER BY nome;"

echo ""
echo ">> Limpando eventuais travas residuais de teste no Redis..."
docker compose exec -T redis redis-cli DEL \
  "lock:estoque:11111111-1111-1111-1111-111111111111" \
  "lock:estoque:22222222-2222-2222-2222-222222222222" \
  "lock:estoque:33333333-3333-3333-3333-333333333333" \
  "lock:estoque:44444444-4444-4444-4444-444444444444" || true

echo ""
echo "=========================================================="
echo "  ✅ Estoque restaurado com sucesso!"
echo "  💧 Água mineral resetada para 1 un (pronta para corrida)."
echo "  🌐 Acesse a interface web e clique em 'Atualizar Estoque'."
echo "=========================================================="
