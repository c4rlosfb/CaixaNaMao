#!/bin/bash
# =============================================================================
# Hook de initdb.d — libera conexões de replicação no pg_hba.conf (issue #11).
#
# Roda UMA vez, na primeira inicialização do volume, logo depois do initdb e
# antes de o servidor definitivo subir. É o complemento do
# primary-entrypoint.sh: lá a regra é adicionada em volumes JÁ inicializados
# (o pg_hba.conf não existe antes do initdb); aqui ela é adicionada no volume
# NOVO, cujo pg_hba.conf acabou de ser gerado.
#
# Sem esta regra, o pg_basebackup da réplica falha com
# "no pg_hba.conf entry for replication connection" — as linhas que o initdb
# cria cobrem apenas o loopback do próprio container.
# =============================================================================
set -Eeuo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PG_HBA="$PGDATA/pg_hba.conf"

if ! grep -q '^# caixanamao: replica' "$PG_HBA"; then
    {
        echo ""
        echo "# caixanamao: replica — permite a réplica do Compose autenticar"
        echo "host replication replicator all scram-sha-256"
    } >> "$PG_HBA"
    echo "==> [initdb] regra de replicação adicionada ao pg_hba.conf"
fi
