#!/bin/bash
# =============================================================================
# Entrypoint do PostgreSQL réplica (hot standby) — CaixaNaMão
#
# Responsabilidades:
#   1. na primeira inicialização, clonar o primário com pg_basebackup usando o
#      slot físico criado por infra/replicacao/init-replicacao.sql;
#   2. escrever o `postgresql.auto.conf` da réplica (primary_conninfo + slot) e
#      o `standby.signal`, que é o que a coloca em recovery contínua;
#   3. iniciar o PostgreSQL pelo entrypoint oficial da imagem.
#
# A senha do papel de replicação NÃO é gravada no PGDATA: ela vai para um
# `.pgpass` (0600, dono `postgres`) fora do volume de dados e é reaproveitada
# pelo processo de WAL receiver via PGPASSFILE. Recriar o container não perde
# a configuração de recovery, mas o `.pgpass` é reescrito a cada start.
#
# Se o volume da réplica for reutilizado depois de o primário ser recriado
# (novo system identifier), o PostgreSQL recusa a recovery e o container entra
# em loop de restart. Nesse caso, remova o volume e suba de novo — o clone é
# refeito do zero: `docker compose rm -sf postgres-replica && docker volume rm
# caixanamao_postgres_replica_data`.
# =============================================================================
set -Eeuo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
PRIMARY_HOST="${PRIMARY_HOST:-postgres}"
PRIMARY_PORT="${PRIMARY_PORT:-5432}"
REPLICATION_USER="${REPLICATION_USER:-replicator}"
REPLICATION_SLOT="${REPLICATION_SLOT:-caixanamao_replica}"
# Marcador de clone concluído: o PGDATA só é considerado pronto quando o
# pg_basebackup terminou E o auto.conf foi escrito. Sem ele, um clone
# interrompido no meio seria confundido com uma réplica válida.
MARCADOR="$PGDATA/.replica_clonada"

log() { printf '==> [postgres-replica] %s\n' "$*"; }

# -----------------------------------------------------------------------------
# 1. A imagem oficial roda como root; os arquivos do PGDATA precisam ser do
#    usuário postgres. Reexecuta o próprio script como postgres (como o
#    docker-entrypoint.sh oficial faz).
# -----------------------------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$PGDATA"
    chown -R postgres:postgres "$PGDATA"
    exec gosu postgres /bin/bash "$0" "$@"
fi

: "${REPLICATION_PASSWORD:?REPLICATION_PASSWORD nao definida — ver .env.example}"

# HOME explícito: o gosu não garante HOME=/var/lib/postgresql, e o libpq procura
# o .pgpass em $HOME (ou em PGPASSFILE, que fixamos abaixo).
export HOME=/var/lib/postgresql
export PGPASSFILE="$HOME/.pgpass"
# Cria o arquivo já com permissão restrita (libpq ignora .pgpass com modo
# aberto). O umask fica restrito apenas nesta subshell.
(
    umask 077
    printf '%s:%s:*:%s:%s\n' \
        "$PRIMARY_HOST" "$PRIMARY_PORT" "$REPLICATION_USER" "$REPLICATION_PASSWORD" \
        > "$PGPASSFILE"
)
chmod 600 "$PGPASSFILE"

# -----------------------------------------------------------------------------
# 2. Clone inicial (apenas uma vez por volume)
# -----------------------------------------------------------------------------
if [ ! -f "$MARCADOR" ]; then
    log "primeira inicialização: aguardando o primário ${PRIMARY_HOST}:${PRIMARY_PORT}"
    pronto="nao"
    for _ in $(seq 1 60); do
        if PGPASSWORD="$REPLICATION_PASSWORD" pg_isready \
            -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPLICATION_USER" \
            -d postgres >/dev/null 2>&1; then
            pronto="sim"
            break
        fi
        sleep 2
    done
    if [ "$pronto" != "sim" ]; then
        log "ERRO: primário não respondeu em 120s (papel/regra de replicação configurados?)"
        exit 1
    fi

    log "clonando o primário com pg_basebackup (slot ${REPLICATION_SLOT})"
    find "$PGDATA" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
        --host="$PRIMARY_HOST" \
        --port="$PRIMARY_PORT" \
        --username="$REPLICATION_USER" \
        --pgdata="$PGDATA" \
        --format=plain \
        --wal-method=stream \
        --slot="$REPLICATION_SLOT" \
        --checkpoint=fast \
        --progress \
        --verbose

    # standby.signal + auto.conf: a réplica sobe em recovery contínua.
    touch "$PGDATA/standby.signal"
    cat > "$PGDATA/postgresql.auto.conf" <<EOF
# Arquivo gerado por infra/postgres/replica-entrypoint.sh — não editar à mão.
primary_conninfo = 'host=$PRIMARY_HOST port=$PRIMARY_PORT user=$REPLICATION_USER application_name=postgres-replica'
primary_slot_name = '$REPLICATION_SLOT'
recovery_target_timeline = 'latest'
hot_standby = on
hot_standby_feedback = on
EOF
    chmod 600 "$PGDATA/postgresql.auto.conf"
    touch "$MARCADOR"
    log "clone concluído — iniciando hot standby"
else
    log "PGDATA já clonado; retomando a replicação"
fi

# -----------------------------------------------------------------------------
# 3. Sobe o PostgreSQL pelo entrypoint oficial (PGDATA já existe, então ele só
#    executa o servidor com os argumentos repassados pelo Compose).
# -----------------------------------------------------------------------------
exec /usr/local/bin/docker-entrypoint.sh "$@"
