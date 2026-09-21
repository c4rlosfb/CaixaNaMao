#!/usr/bin/env bash
# =============================================================================
# Deploy de homologação do CaixaNaMão (Sprint 2) numa máquina com Docker.
#
#   bash scripts/deploy.sh          # sobe a stack e espera os health checks
#   bash scripts/deploy.sh --down   # derruba a stack
#   SERVICOS="postgres redis servico-estoque servico-pedidos localstack" \
#       bash scripts/deploy.sh      # inclui o LocalStack (SQS) na subida
#
# Pré-requisitos: docker + docker compose (>= 2.24, por causa do `!override`).
# Em Ubuntu:
#   sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
#   sudo usermod -aG docker "$USER"     # e reconecte o SSH para valer
#
# O script:
#   1. valida Docker/Compose;
#   2. cria o .env com segredos aleatórios na primeira execução (nunca versionado);
#   3. sobe a stack com o override de homologação (só a 8000 fica pública);
#   4. espera os health checks e imprime as URLs de teste.
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

SERVICOS="${SERVICOS:-postgres redis servico-estoque servico-pedidos}"
ESPERA_SEGUNDOS="${ESPERA_SEGUNDOS:-240}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.homolog.yml)

if [ "${1:-}" = "--down" ]; then
    echo "==> derrubando a stack de homologação"
    "${COMPOSE[@]}" down
    exit 0
fi

# --- 1. Docker e Compose ----------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    cat >&2 <<'FIM'
ERRO: docker não encontrado. Instale com:
  sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
  sudo usermod -aG docker "$USER"     # depois reconecte o SSH
FIM
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "ERRO: 'docker compose' (v2) não disponível. Instale o pacote docker-compose-v2." >&2
    exit 1
fi
echo "==> docker: $(docker --version)"
echo "==> compose: $(docker compose version --short 2>/dev/null || docker compose version | head -1)"

# --- 2. .env com segredos gerados ------------------------------------------
if [ ! -f .env ]; then
    echo "==> criando .env a partir de .env.example com segredos aleatórios"
    cp .env.example .env
    gerar_segredo() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(gerar_segredo)|" .env
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(gerar_segredo)|" .env
    echo "    .env criado (não versionado). Para trocar valores: edite .env e rode de novo."
else
    echo "==> usando o .env existente"
fi

# --- 3. subindo a stack -----------------------------------------------------
echo "==> docker compose up -d --build ${SERVICOS}"
"${COMPOSE[@]}" up -d --build ${SERVICOS}

# --- 4. espera os health checks --------------------------------------------
echo "==> aguardando os serviços ficarem saudáveis (até ${ESPERA_SEGUNDOS}s)"
inicio="$(date +%s)"
until curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 &&
    curl -fsS http://127.0.0.1:8002/health >/dev/null 2>&1; do
    if [ "$(( $(date +%s) - inicio ))" -gt "$ESPERA_SEGUNDOS" ]; then
        cat >&2 <<FIM
ERRO: os serviços não ficaram saudáveis em ${ESPERA_SEGUNDOS}s. Diagnóstico:
  ${COMPOSE[*]} ps
  ${COMPOSE[*]} logs --tail=80 servico-pedidos servico-estoque
FIM
        exit 1
    fi
    sleep 5
done

# IP público pela metadata da EC2 (IMDSv2); se não for EC2, mostra um placeholder.
obter_ip_publico() {
    local token
    token="$(curl -fsS -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
    if [ -n "$token" ]; then
        curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $token" \
            http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true
    fi
}
IP_PUBLICO="$(obter_ip_publico)"
IP_PUBLICO="${IP_PUBLICO:-<ip-publico-da-instancia>}"

echo
echo "==================== CaixaNaMão no ar ===================="
"${COMPOSE[@]}" ps
echo
echo "Teste da sua máquina:"
echo "  curl http://${IP_PUBLICO}:8000/health"
echo
echo "Suíte de integração (roda na própria instância):"
echo "  pip install -r tests/integracao/requirements.txt"
echo "  pytest tests/integracao -q"
echo
echo "Derrubar a stack: bash scripts/deploy.sh --down"
echo "=========================================================="
