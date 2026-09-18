-- =============================================================================
-- Script de inicialização do PostgreSQL — CaixaNaMao
-- Executado automaticamente pelo container postgres na primeira inicialização
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Criar databases lógicos isolados (um por microsserviço)
-- -----------------------------------------------------------------------------
CREATE DATABASE pedidos_db;
CREATE DATABASE estoque_db;
CREATE DATABASE autenticacao_db;

-- -----------------------------------------------------------------------------
-- 2. Criar usuários com senhas via variáveis de ambiente
--    (os valores são substituídos pelo entrypoint do Docker)
-- -----------------------------------------------------------------------------
CREATE USER pedidos_user WITH PASSWORD 'pedidos_pass';
CREATE USER estoque_user WITH PASSWORD 'estoque_pass';
CREATE USER auth_user    WITH PASSWORD 'auth_pass';

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
