-- =============================================================================
-- Bootstrap da replicação física — CaixaNaMão (Sprint 3 / issue #11)
--
-- Executado pelo serviço one-shot `replicacao-init` a cada `docker compose up`,
-- conectado ao primário por TCP como superusuário. É IDEMPOTENTE: pode rodar
-- quantas vezes for preciso, inclusive em volumes criados nas Sprints 1/2.
--
-- Cria:
--   1. o papel `replicator` (LOGIN + REPLICATION), com a senha vinda do
--      ambiente (REPLICATION_PASSWORD) — nunca hardcoded;
--   2. o slot físico `caixanamao_replica`, que retém no primário o WAL que a
--      réplica ainda não consumiu. Sem slot, um `wal_keep_size` pequeno pode
--      descartar WAL e obrigar a réplica a ser clonada de novo.
--
-- O `pg_hba.conf` é ajustado pelo primary-entrypoint.sh (regra
-- `host replication replicator all scram-sha-256`), porque o arquivo não é
-- acessível por SQL e precisa existir antes de o servidor subir.
-- =============================================================================

\getenv replication_password REPLICATION_PASSWORD

\if :{?replication_password}
\else
DO $guard$
BEGIN
    RAISE EXCEPTION 'REPLICATION_PASSWORD nao definida — ver .env.example';
END
$guard$;
\endif

-- 1. Papel de replicação -----------------------------------------------------
-- \gexec executa o CREATE somente se o papel ainda não existir; o ALTER abaixo
-- garante convergência da senha/atributos mesmo em volumes antigos.
SELECT format(
    'CREATE ROLE replicator WITH LOGIN REPLICATION PASSWORD %L',
    :'replication_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator')
\gexec

ALTER ROLE replicator WITH LOGIN REPLICATION PASSWORD :'replication_password';

-- 2. Slot físico --------------------------------------------------------------
-- O slot é criado na posição atual do WAL. A réplica usa
-- `primary_slot_name = 'caixanamao_replica'` (ver replica-entrypoint.sh).
SELECT pg_create_physical_replication_slot('caixanamao_replica')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_replication_slots WHERE slot_name = 'caixanamao_replica'
);

-- 3. Resumo (aparece no log do serviço one-shot) -------------------------------
SELECT
    (SELECT rolname FROM pg_roles WHERE rolname = 'replicator')    AS papel,
    (SELECT slot_name FROM pg_replication_slots
      WHERE slot_name = 'caixanamao_replica')                      AS slot,
    current_setting('wal_level')                                   AS wal_level;
