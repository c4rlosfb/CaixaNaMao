-- =============================================================================
-- Script de inicialização do PostgreSQL — CaixaNaMao
-- Executado automaticamente pelo container postgres na primeira inicialização
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Credenciais dos usuários de serviço (lidas do ambiente)
--
-- IMPORTANTE: o entrypoint oficial do postgres executa arquivos `.sql` pelo psql
-- *verbatim* — apenas arquivos `.sh` passam pelo `source` do shell, então NÃO há
-- expansão de `${VAR}` dentro deste arquivo. Para manter o script em .sql e não
-- deixar senhas hardcoded, as credenciais são lidas do ambiente pelo próprio
-- psql com `\getenv` (disponível no PostgreSQL 16), a partir das variáveis
-- definidas no `docker-compose.yml`:
--     PEDIDOS_DB_PASSWORD / ESTOQUE_DB_PASSWORD / AUTH_DB_PASSWORD
--
-- Se alguma delas não estiver definida, a inicialização falha explicitamente
-- (fail closed) em vez de criar usuários com senha vazia.
-- Referência: code review do PR #25 — item bloqueador 1.
-- -----------------------------------------------------------------------------
\getenv pedidos_db_password      PEDIDOS_DB_PASSWORD
\getenv estoque_db_password      ESTOQUE_DB_PASSWORD
\getenv autenticacao_db_password AUTH_DB_PASSWORD

\if :{?pedidos_db_password}
\else
DO $guard$
BEGIN
    RAISE EXCEPTION 'PEDIDOS_DB_PASSWORD nao definida — ver docker-compose.yml';
END
$guard$;
\endif

\if :{?estoque_db_password}
\else
DO $guard$
BEGIN
    RAISE EXCEPTION 'ESTOQUE_DB_PASSWORD nao definida — ver docker-compose.yml';
END
$guard$;
\endif

\if :{?autenticacao_db_password}
\else
DO $guard$
BEGIN
    RAISE EXCEPTION 'AUTH_DB_PASSWORD nao definida — ver docker-compose.yml';
END
$guard$;
\endif

-- -----------------------------------------------------------------------------
-- 1. Criar databases lógicos isolados (um por microsserviço)
-- -----------------------------------------------------------------------------
CREATE DATABASE pedidos_db;
CREATE DATABASE estoque_db;
CREATE DATABASE autenticacao_db;

-- -----------------------------------------------------------------------------
-- 2. Criar usuários com as senhas vindas do ambiente
--    (as mesmas variáveis consumidas pelo docker-compose.yml nos DATABASE_URL
--     dos microsserviços — fonte única de verdade das credenciais)
-- -----------------------------------------------------------------------------
CREATE USER pedidos_user WITH PASSWORD :'pedidos_db_password';
CREATE USER estoque_user WITH PASSWORD :'estoque_db_password';
CREATE USER auth_user    WITH PASSWORD :'autenticacao_db_password';

-- -----------------------------------------------------------------------------
-- 3. Revogar acesso público padrão do PostgreSQL
--    Por padrão o PG concede CONNECT a PUBLIC — isso quebra o isolamento.
--    Referência: ADR-003 + correção do code review PR #23
-- -----------------------------------------------------------------------------
REVOKE CONNECT ON DATABASE pedidos_db      FROM PUBLIC;
REVOKE CONNECT ON DATABASE estoque_db      FROM PUBLIC;
REVOKE CONNECT ON DATABASE autenticacao_db FROM PUBLIC;

-- -----------------------------------------------------------------------------
-- 4. Conceder acesso apenas ao usuário correspondente a cada database
-- -----------------------------------------------------------------------------
GRANT CONNECT ON DATABASE pedidos_db      TO pedidos_user;
GRANT CONNECT ON DATABASE estoque_db      TO estoque_user;
GRANT CONNECT ON DATABASE autenticacao_db TO auth_user;

-- -----------------------------------------------------------------------------
-- 5. Conceder privilégios de schema dentro de cada database
--    (executados em sessões separadas via \connect no entrypoint)
-- -----------------------------------------------------------------------------
\connect pedidos_db
GRANT ALL PRIVILEGES ON SCHEMA public TO pedidos_user;

\connect estoque_db
GRANT ALL PRIVILEGES ON SCHEMA public TO estoque_user;

\connect autenticacao_db
GRANT ALL PRIVILEGES ON SCHEMA public TO auth_user;
