# Suíte de integração Pedidos <-> Estoque

Verificação da **chamada gRPC entre o `servico-pedidos` e o `servico-estoque`**
(issue #7), com o efeito conferido direto no PostgreSQL.

O contrato é `shared-protos/estoque.proto` — esta suíte é o que garante que as duas
pontas continuam falando a mesma língua depois de qualquer mudança.

## Como rodar

```bash
# 1. dependências da suíte
pip install -r tests/integracao/requirements.txt

# 2. sobe a stack e roda tudo (recomendado)
bash scripts/validar_integracao.sh

# ou, com a stack já no ar:
pytest tests/integracao -v

# só a guarda de contrato (não precisa de stack):
pytest tests/integracao/test_contrato_estoque.py -v
```

O script sobe por padrão apenas `postgres redis servico-estoque servico-pedidos` — o
localstack (SQS) não participa desta validação, porque a publicação de eventos é
best-effort (ADR-005) e o consumo é de outra issue. Para subir a stack inteira:

```bash
SERVICOS="$(docker compose config --services)" bash scripts/validar_integracao.sh
```

Sem a stack no ar, os testes de integração são **pulados** com o motivo e as
instruções no relatório — a suíte nunca fica vermelha por ambiente ausente, e
também nunca passa fingindo que verificou algo.

## Variáveis de ambiente

| Variável | Default | Para que serve |
|---|---|---|
| `PEDIDOS_BASE_URL` | `http://localhost:8000` | REST do `servico-pedidos` |
| `ESTOQUE_GRPC_ADDRESS` | `localhost:50051` | gRPC do `servico-estoque` |
| `ESTOQUE_DATABASE_URL` | `postgresql://estoque_user:estoque_pass@localhost:5432/estoque_db` | semear e observar itens |
| `JWT_SECRET` | `dev-secret-troque-em-producao` | assinar o token do vendedor (HS256) |

## O que é verificado

| Arquivo | Cobre |
|---|---|
| `test_contrato_estoque.py` | estrutura do `.proto`: serviço, 3 RPCs unárias, e nome/número/tipo de cada campo das mensagens (renumerar campo quebraria a compatibilidade de fio) |
| `test_integracao_pedidos_estoque.py` | `ConsultarItem`; pedido com **múltiplos itens** (um `CheckAndReserve` por item, mesmo `pedido_id`); retry com a mesma `Idempotency-Key`; **compensação** quando um item posterior não tem estoque; cancelamento devolvendo o estoque de todos os itens; isolamento entre vendedores; `NOT_FOUND`/`INVALID_ARGUMENT` do contrato; `ReleaseReserva` idempotente; reservas independentes por pedido |

## Decisões

- **Black-box**: a suíte não importa código dos serviços; fala HTTP/gRPC/SQL como um
  cliente externo. É a forma de validar o contrato de fora para dentro — se um
  serviço mudar a implementação sem quebrar o contrato, a suíte continua válida.
- **Itens semeados por SQL**: o contrato não expõe criação de item (cadastro não faz
  parte da issue), então a suíte insere na tabela `itens` e limpa o que criou
  (incluindo o ledger `movimentacoes`).
- **Stubs gerados em tempo de execução**: nada de `*_pb2.py` versionado — o `protoc`
  compila o contrato em um diretório temporário a cada execução, o que também prova
  que o contrato continua compilável.
- **SQS fora do escopo**: a publicação de eventos é best-effort (ADR-005) e o
  consumo é de outra issue; esta suíte verifica o caminho síncrono Pedidos->Estoque.
- **Limpeza**: a suíte apaga os itens que semeia (e o ledger `movimentacoes`
  correspondente). Os **pedidos** criados por ela permanecem no `pedidos_db` de
  desenvolvimento — a API REST não expõe exclusão de pedido e apagar direto no banco
  esconderia justamente o que se quer observar. Rode com um banco descartável se
  precisar de um estado limpo.
