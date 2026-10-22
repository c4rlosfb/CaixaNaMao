#!/bin/bash
# =============================================================================
# Bootstrap da replicação com retry — issue #11.
#
# O serviço `replicacao-init` depende do healthcheck do primário, mas em volume
# NOVO o initdb sobe um servidor temporário que escuta apenas no socket Unix e
# pode satisfazer um `pg_isready` local antes de o servidor definitivo abrir a
# porta TCP. Este wrapper tenta conectar algumas vezes, em vez de falhar na
# primeira tentativa, e só retorna erro se o primário continuar inacessível.
# =============================================================================
set -Eeuo pipefail

TENTATIVAS="${TENTATIVAS:-30}"
INTERVALO="${INTERVALO:-2}"

for tentativa in $(seq 1 "$TENTATIVAS"); do
    if psql --no-psqlrc -v ON_ERROR_STOP=1 -f /infra/init-replicacao.sql; then
        echo "==> [replicacao-init] bootstrap concluído na tentativa ${tentativa}"
        exit 0
    fi
    echo "==> [replicacao-init] primário ainda não aceita conexões TCP (tentativa ${tentativa}/${TENTATIVAS})" >&2
    sleep "$INTERVALO"
done

echo "ERRO: não foi possível configurar a replicação após ${TENTATIVAS} tentativas" >&2
exit 1
