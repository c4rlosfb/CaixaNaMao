#!/usr/bin/env bash
# =============================================================================
# Valida a replicação PostgreSQL master/slave — Sprint 3 / issue #11.
#
#   bash scripts/validar_replicacao.sh
#
# Sobe primário + bootstrap + réplica, espera o hot standby ficar pronto e
# executa verificações REAIS (não apenas "o container subiu"):
#
#   1. a réplica está em recovery (`pg_is_in_recovery() = true`);
#   2. o primário reporta o WAL receiver em `streaming`;
#   3. o primário está com `wal_level = replica` e o slot físico existe;
#   4. os três databases lógicos foram replicados;
#   5. uma escrita no primário aparece na réplica (consistência dos dados);
#   6. a réplica recusa escrita (`read-only transaction`) — é standby, não master;
#   7. imprime o estado do `pg_stat_replication` e o lag em bytes.
#
# Sai com código != 0 se qualquer verificação falhar. A tabela temporária usada
# no teste 5 é removida ao final (trap), inclusive em caso de erro.
#
# Requisitos: Docker Desktop em execução e `.env`/defaults de desenvolvimento
# (as mesmas variáveis dos outros scripts do repositório).
# =============================================================================
set -Eeuo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

COMPOSE=(docker compose)
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres_dev}"
export PEDIDOS_DB_PASSWORD="${PEDIDOS_DB_PASSWORD:-pedidos_pass}"
export ESTOQUE_DB_PASSWORD="${ESTOQUE_DB_PASSWORD:-estoque_pass}"
export AUTH_DB_PASSWORD="${AUTH_DB_PASSWORD:-auth_pass}"
export REPLICATION_PASSWORD="${REPLICATION_PASSWORD:-replicacao_pass}"

TABELA="_validacao_replicacao"
USUARIO="${POSTGRES_USER:-postgres}"
falhas=0

ok()    { printf '  [OK]    %s\n' "$*"; }
falha() { printf '  [FALHA] %s\n' "$*" >&2; falhas=$((falhas + 1)); }

psql_primario()  { "${COMPOSE[@]}" exec -T postgres         psql -U "$USUARIO" -d "$1" -tAc "$2" 2>/dev/null | tr -d '\r'; }
psql_replica()   { "${COMPOSE[@]}" exec -T postgres-replica psql -U "$USUARIO" -d "$1" -tAc "$2" 2>/dev/null | tr -d '\r'; }

limpar() {
    "${COMPOSE[@]}" exec -T postgres psql -U "$USUARIO" -d pedidos_db \
        -q -c "DROP TABLE IF EXISTS ${TABELA};" >/dev/null 2>&1 || true
}
trap limpar EXIT

echo "==> subindo primário, bootstrap da replicação e réplica"
"${COMPOSE[@]}" up -d postgres replicacao-init postgres-replica >/dev/null

echo "==> aguardando o hot standby aceitar consultas (até 120s)"
pronta="nao"
for _ in $(seq 1 60); do
    if [ "$(psql_replica postgres 'SELECT 1')" = "1" ]; then
        pronta="sim"
        break
    fi
    sleep 2
done
if [ "$pronta" != "sim" ]; then
    echo "ERRO: a réplica não ficou pronta. Diagnóstico:" >&2
    echo "  docker compose logs --tail=80 postgres postgres-replica replicacao-init" >&2
    exit 1
fi

echo
echo "==> verificações"

# 1. Réplica em recovery -------------------------------------------------------
recovery="$(psql_replica postgres 'SELECT pg_is_in_recovery()')"
if [ "$recovery" = "t" ]; then
    ok "réplica em recovery (pg_is_in_recovery = true)"
else
    falha "réplica NÃO está em recovery (pg_is_in_recovery = '$recovery')"
fi

# 2. Streaming ativo -----------------------------------------------------------
streaming="$(psql_primario postgres "SELECT count(*) FROM pg_stat_replication WHERE application_name = 'postgres-replica' AND state = 'streaming'")"
if [ "$streaming" = "1" ]; then
    ok "primário com WAL sender em streaming para postgres-replica"
else
    falha "nenhum WAL sender em streaming (count = '$streaming')"
fi

# 3. Configuração do primário --------------------------------------------------
wal_level="$(psql_primario postgres 'SHOW wal_level')"
if [ "$wal_level" = "replica" ]; then
    ok "primário com wal_level = replica"
else
    falha "wal_level do primário é '$wal_level' (esperado: replica)"
fi

slot="$(psql_primario postgres "SELECT count(*) FROM pg_replication_slots WHERE slot_name = 'caixanamao_replica' AND slot_type = 'physical'")"
if [ "$slot" = "1" ]; then
    ok "slot físico caixanamao_replica presente no primário"
else
    falha "slot físico caixanamao_replica ausente (count = '$slot')"
fi

# 4. Databases lógicos replicados ---------------------------------------------
databases="$(psql_replica postgres "SELECT count(*) FROM pg_database WHERE datname IN ('pedidos_db','estoque_db','autenticacao_db')")"
if [ "$databases" = "3" ]; then
    ok "os 3 databases lógicos existem na réplica"
else
    falha "databases replicados = '$databases' (esperado: 3)"
fi

# 5. Escrita no primário aparece na réplica ------------------------------------
id="$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
psql_primario pedidos_db "CREATE TABLE IF NOT EXISTS ${TABELA} (id text PRIMARY KEY, criado_em timestamptz NOT NULL DEFAULT now()); INSERT INTO ${TABELA} (id) VALUES ('${id}');" >/dev/null
replicou="nao"
for _ in $(seq 1 20); do
    if [ "$(psql_replica pedidos_db "SELECT count(*) FROM ${TABELA} WHERE id = '${id}'")" = "1" ]; then
        replicou="sim"
        break
    fi
    sleep 1
done
if [ "$replicou" = "sim" ]; then
    ok "escrita no primário (pedidos_db) visível na réplica em <20s"
else
    falha "escrita no primário não apareceu na réplica"
fi

# 6. Réplica recusa escrita ----------------------------------------------------
set +e
saida_escrita="$("${COMPOSE[@]}" exec -T postgres-replica psql -U "$USUARIO" -d pedidos_db \
    -c "CREATE TABLE escrita_na_replica (id int);" 2>&1)"
status_escrita=$?
set -e
if [ "$status_escrita" -ne 0 ] && grep -qi "read-only" <<< "$saida_escrita"; then
    ok "réplica recusa escrita (read-only transaction)"
else
    falha "réplica aceitou (ou falhou por outro motivo) uma escrita: $saida_escrita"
fi

# 7. Estado e lag --------------------------------------------------------------
echo
echo "==> pg_stat_replication (primário)"
"${COMPOSE[@]}" exec -T postgres psql -U "$USUARIO" -d postgres -c \
    "SELECT application_name, state, sync_state,
            pg_wal_lsn_diff(sent_lsn, replay_lsn) AS lag_bytes
       FROM pg_stat_replication;"

echo
if [ "$falhas" -eq 0 ]; then
    echo "==> REPLICAÇÃO VALIDADA: 7/7 verificações passaram"
    exit 0
fi
echo "==> FALHA: ${falhas} verificação(ões) não passaram" >&2
exit 1
