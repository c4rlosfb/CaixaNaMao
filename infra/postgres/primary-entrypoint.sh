#!/bin/bash
# =============================================================================
# Entrypoint do PostgreSQL primário (master) — CaixaNaMão
#
# Encapsula o entrypoint oficial da imagem `postgres:16` para uma única tarefa:
# garantir, em TODO start, que o `pg_hba.conf` aceite conexões de replicação
# vindas da rede do Compose.
#
# Por que isso não pode ficar só no init-db.sql: os scripts de
# /docker-entrypoint-initdb.d rodam **apenas na primeira inicialização** do
# volume. Quem já tinha o volume `postgres_data` criado (Sprints 1/2) nunca
# executaria a regra nova. Como o pg_hba.conf só é lido na inicialização do
# servidor, a linha é acrescentada aqui — antes do `postgres` subir — e o
# volume antigo passa a aceitar a réplica sem precisar ser recriado.
#
# A criação do papel `replicator` e do slot físico é feita pelo serviço
# `replicacao-init` (ver infra/replicacao/init-replicacao.sql), que roda uma
# vez por `docker compose up`, também de forma idempotente.
# =============================================================================
set -Eeuo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PG_HBA="$PGDATA/pg_hba.conf"

if [ -f "$PGDATA/PG_VERSION" ]; then
    # Volume já inicializado: acrescenta a regra se ela ainda não existir.
    # O initdb já cria linhas `host replication all 127.0.0.1/32 trust` para o
    # loopback; elas NÃO cobrem a rede do Compose, por isso o marcador próprio.
    if ! grep -q '^# caixanamao: replica' "$PG_HBA"; then
        {
            echo ""
            echo "# caixanamao: replica — permite a réplica do Compose autenticar"
            echo "host replication replicator all scram-sha-256"
        } >> "$PG_HBA"
        echo "==> [postgres] regra de replicação adicionada ao pg_hba.conf"
    fi
else
    # Volume novo: o initdb ainda vai rodar dentro do entrypoint oficial; a
    # regra é criada logo depois pelo hook initdb-replicacao.sh (initdb.d).
    echo "==> [postgres] volume novo — pg_hba.conf será criado pelo initdb"
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
