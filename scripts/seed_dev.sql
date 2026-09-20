-- =============================================================================
-- Seed de desenvolvimento — itens de exemplo para exercitar o servico-estoque
-- (o contrato gRPC não expõe criação de item; em dev os itens entram por aqui).
--
-- Uso:
--   docker exec -i <container_postgres> psql -U postgres -d estoque_db \
--       < scripts/seed_dev.sql
--
-- É idempotente: pode rodar quantas vezes quiser (ON CONFLICT DO NOTHING).
-- O último item tem estoque 1 de propósito: é o caso da demonstração de
-- concorrência (duas reservas simultâneas do "último item").
-- =============================================================================

INSERT INTO itens (id, nome, descricao, preco, quantidade_disponivel, quantidade_reservada)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'Coxinha',
     'Salgado de frango assado', 5.50, 10, 0),
    ('22222222-2222-2222-2222-222222222222', 'Pastel de carne',
     'Pastel frito na hora', 7.00, 5, 0),
    ('33333333-3333-3333-3333-333333333333', 'Refrigerante lata',
     'Lata 350ml gelada', 6.00, 20, 0),
    ('44444444-4444-4444-4444-444444444444', 'Água mineral',
     'Garrafa 500ml — estoque unitário para o teste de concorrência', 3.00, 1, 0)
ON CONFLICT (id) DO NOTHING;
