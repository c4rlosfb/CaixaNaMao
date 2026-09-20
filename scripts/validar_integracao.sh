#!/usr/bin/env bash
# =============================================================================
# Valida a integração Pedidos <-> Estoque de ponta a ponta (issue #7).
#
#   bash scripts/validar_integracao.sh
#
# Sobe a stack com Docker Compose, espera os serviços ficarem saudáveis e roda a
# suíte de integração (tests/integracao): HTTP no servico-pedidos, gRPC no
# servico-estoque e conferência do efeito no PostgreSQL. Se a stack não subir, o
# script FALHA (não deixa a suíte "pular" e parecer verde).
#
# Requisitos: docker + docker compose e as dependências de teste
#   pip install -r tests/integracao/requirements.txt
#
# O compose exige POSTGRES_PASSWORD, *_DB_PASSWORD e JWT_SECRET: em
# desenvolvimento usamos os defaults do .env.example (exporte antes para
# sobrescrever).
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PYTHON_BIN="${PYTHON:-python}"

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres_dev}"
export ESTOQUE_DB_PASSWORD="${ESTOQUE_DB_PASSWORD:-estoque_pass}"
export PEDIDOS_DB_PASSWORD="${PEDIDOS_DB_PASSWORD:-pedidos_pass}"
export JWT_SECRET="${JWT_SECRET:-dev-secret-troque-em-producao}"

# Serviços necessários para ESTA validação: os dois serviços e as suas dependências
# (PostgreSQL e Redis). O localstack (SQS) fica de fora de propósito: a publicação de
# eventos é best-effort (ADR-005), o consumo é de outra issue, e subir só o necessário
# evita um download de ~300MB que não tem relação com a chamada Pedidos->Estoque.
# Para validar a stack inteira: SERVICOS="$(docker compose config --services)" bash ...
SERVICOS="${SERVICOS:-postgres redis servico-estoque servico-pedidos}"

echo "==> subindo a stack (docker compose up -d --build ${SERVICOS})"
docker compose up -d --build ${SERVICOS}

echo "==> aguardando servico-pedidos (:8000) e servico-estoque (:8002) ficarem prontos"
pronto="nao"
for _ in $(seq 1 40); do
    if curl -fsS http://localhost:8000/health >/dev/null 2>&1 &&
        curl -fsS http://localhost:8002/health >/dev/null 2>&1; then
        pronto="sim"
        break
    fi
    sleep 3
done

if [ "$pronto" != "sim" ]; then
    echo "ERRO: a stack não ficou pronta a tempo." >&2
    echo "Veja os logs com: docker compose logs servico-pedidos servico-estoque" >&2
    exit 1
fi

echo "==> rodando a suíte de integração"
"$PYTHON_BIN" -m pytest tests/integracao -v
