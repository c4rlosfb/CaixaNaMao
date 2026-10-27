# Decisões Técnicas — Architecture Decision Records (ADRs)

> **Projeto:** CaixaNaMao  
> **Sprint:** 1  
> **Autor:** c4rlosfb  
> **Status:** Aprovado pela equipe  
> **Última atualização:** 2026-09-18

Cada ADR segue o formato: **Contexto → Decisão → Justificativa → Consequências → Alternativas rejeitadas**.

---

## ADR-001: gRPC para Comunicação Interna entre Microsserviços

**Status:** Aceito

### Contexto

O `servico-pedidos` precisa verificar e reservar estoque no `servico-estoque` de forma
síncrona e em tempo real durante o fluxo de criação de pedido. Essa chamada é feita
sob alta concorrência (múltiplos terminais simultâneos) e é o ponto mais
latência-sensível do sistema.

### Decisão

A comunicação **síncrona entre `servico-pedidos` e `servico-estoque`** utilizará **gRPC** com contratos definidos em
Protocol Buffers (`.proto`) centralizados na pasta `shared-protos/`.

Escopo desta decisão: a chamada crítica `CheckAndReserve` (Pedidos → Estoque). A comunicação entre outros serviços
(ex: validação de JWT no `servico-pedidos`) usa validação local HS256, sem chamada de rede adicional.

### Justificativa

| Critério | gRPC | REST/JSON |
|---|---|---|
| **Serialização** | Binária (Protobuf) — ~5–10× menor que JSON | Textual — verboso |
| **Tipagem** | Forte — contrato compilado, erros em build time | Fraca — erros só em runtime |
| **Protocolo** | HTTP/2 — multiplexado, sem head-of-line blocking | HTTP/1.1 — sem multiplexação |
| **Latência** | Menor (binário + HTTP/2) | Maior (parsing JSON + HTTP/1.1) |
| **Contrato explícito** | `.proto` é a fonte da verdade entre equipes | Depende de documentação informal |

Em um sistema onde a reserva de estoque deve completar dentro de um lock Redis com
TTL de 5 segundos, reduzir a latência de comunicação é crítico.

### Consequências

- **Positivas:** Latência interna prevista < 50 ms; contratos explícitos evitam regressões de integração.
- **Negativas:** Curva de aprendizado na geração de stubs (`grpc_tools.protoc`); debugging menos imediato que inspecionar JSON.
- **Ação necessária:** Pipeline de build de cada serviço deve rodar `protoc` para gerar stubs Python a partir de `shared-protos/*.proto`.

### Alternativas Rejeitadas

- **REST/JSON interno:** Mais simples de debugar, mas latência mais alta e sem tipagem garantida em build time.
- **Apache Kafka:** Protocolo assíncrono — inadequado para uma operação síncrona de reserva que bloqueia o usuário aguardando confirmação.

---

## ADR-002: REST/JSON para a API Externa

**Status:** Aceito

### Contexto

Os terminais móveis dos vendedores precisam se comunicar com o backend. Esses
terminais podem ser apps mobile, PWAs ou até clientes HTTP simples. A API precisa
ser simples de consumir, de fácil depuração em campo e compatível com qualquer
plataforma de cliente.

### Decisão

A **API pública** (clientes externos → sistema) será exposta como **REST sobre HTTP/1.1
com payload JSON**, implementada no `servico-pedidos` usando FastAPI.

### Justificativa

| Critério | REST/JSON | gRPC |
|---|---|---|
| **Compatibilidade** | Universal — qualquer HTTP client | Requer biblioteca gRPC no cliente |
| **Debugabilidade** | Alto — curl, Postman, logs legíveis | Baixo — payload binário |
| **Onboarding** | Imediato — padrão da indústria | Requer aprendizado de Protobuf |
| **Ecossistema mobile** | Nativo | Suporte parcial em algumas plataformas |
| **Documentação automática** | FastAPI gera OpenAPI/Swagger | Requer tooling adicional |

A latência adicional do JSON na borda externa é aceitável (< 200 ms p95), pois o
gargalo real está na operação de reserva de estoque, não na serialização da resposta.

### Consequências

- **Positivas:** Qualquer terminal consegue integrar sem dependência de SDK; documentação interativa gerada automaticamente pelo FastAPI (`/docs`).
- **Negativas:** Payload JSON maior que Protobuf; sem verificação de contrato em build time no lado do cliente.
- **Ação necessária:** Manter o schema OpenAPI atualizado a cada alteração de endpoint.

### Alternativas Rejeitadas

- **GraphQL:** Overkill para o caso de uso atual; adiciona complexidade sem benefício tangível com poucos tipos de recursos.
- **gRPC-Web:** Reduziria a latência, mas exige proxy (Envoy/grpc-gateway) e aumenta a complexidade operacional da Sprint 1.

---

## ADR-003: Único Cluster PostgreSQL com Databases Lógicos Isolados

**Status:** Aceito

### Contexto

O padrão de microsserviços recomenda que cada serviço possua seu próprio armazenamento
de dados para garantir isolamento e evitar acoplamento via banco compartilhado.
Entretanto, configurar e operar três servidores PostgreSQL distintos na fase inicial
do projeto adiciona overhead operacional desnecessário.

### Decisão

O sistema utilizará **um único cluster PostgreSQL 16** hospedando **três databases
lógicos independentes**, um por microsserviço:

```
pedidos_db      → exclusivo do servico-pedidos
estoque_db      → exclusivo do servico-estoque
autenticacao_db → exclusivo do servico-autenticacao
```

Cada serviço conecta-se **somente ao seu database** usando um usuário PostgreSQL
com permissões restritas (`GRANT` mínimo). Nenhum serviço conhece ou acessa o
database do outro.

> **Cuidado de bootstrap:** O PostgreSQL concede `CONNECT` a `PUBLIC` por padrão.
> O script de inicialização deve executar `REVOKE CONNECT ON DATABASE <db> FROM PUBLIC`
> em cada database e conceder acesso somente ao usuário correspondente, garantindo
> que credenciais de um serviço não consigam conectar-se ao database de outro.

### Justificativa

| Critério | Cluster Único + DBs Lógicos | 3 Servidores Distintos |
|---|---|---|
| **Isolamento lógico** | ✅ Total (databases distintos) | ✅ Total |
| **Acoplamento de dados** | ✅ Nenhum (credenciais isoladas) | ✅ Nenhum |
| **Complexidade operacional (Sprint 1)** | ✅ Baixa (1 container) | ❌ Alta (3 containers) |
| **Custo de infra (agora)** | ✅ Mínimo | ❌ Triplicado |
| **Preparação para replicação** | ✅ Replicação física do cluster único (Sprint 3+) | ❌ Replicação por servidor |
| **Overhead de configuração** | ✅ Único `docker-compose` entry | ❌ Três entries separados |

O **isolamento lógico é suficiente** para microsserviços enquanto o acesso entre
databases for proibido por design e enforcement de credenciais. O acoplamento que
o padrão busca evitar é o acesso cruzado de dados, não a colocalização física.

### Consequências

- **Positivas:** Ambiente de desenvolvimento simples; um único ponto de replicação física quando Eduardo implementar HA na Sprint 3.
- **Negativas:** Falha do cluster PostgreSQL afeta todos os serviços simultaneamente (single point of failure). Mitigado pela replicação planejada para Sprint 3.
- **Cuidados:** Connection strings devem ser injetadas por variável de ambiente; **nenhum serviço deve receber credenciais de outro database**.
- **Evolução planejada:** Caso a escala exija isolamento físico futuro, a migração para clusters separados é viável sem alteração de código (apenas mudança de string de conexão).

### Alternativas Rejeitadas

- **Banco de dados compartilhado (shared schema):** Viola o princípio de desacoplamento de dados de microsserviços — cria acoplamento implícito via tabelas.
- **3 servidores PostgreSQL distintos:** Correto arquiteturalmente, mas prematuro para Sprint 1; aumenta dívida operacional sem benefício real neste estágio.

---

## ADR-004: Redis para Distributed Locking na Reserva de Estoque

**Status:** Aceito

### Contexto

O requisito central do sistema é evitar que dois terminais reservem simultaneamente
o último item em estoque. O `servico-estoque` pode receber chamadas gRPC concorrentes
para o mesmo `item_id`. Uma operação de leitura-modificação-escrita no PostgreSQL
sem coordenação adicional cria uma race condition clássica.

### Decisão

O `servico-estoque` implementará **distributed locking com Redis** usando o padrão
**SET NX EX** (equivalente ao algoritmo Redlock simplificado para single node):

```
SET lock:estoque:{item_id}  {request_uuid}  NX EX 5
```

- `NX` garante que apenas uma requisição adquira o lock por vez
- `EX 5` garante que o lock expire automaticamente em caso de crash do serviço
- O valor `{request_uuid}` garante que apenas o dono do lock possa liberá-lo (evita liberação acidental por outro processo)

### Justificativa

| Critério | Redis `SET NX EX` | PostgreSQL `SELECT FOR UPDATE` | Mutex em memória |
|---|---|---|---|
| **Distribuído** | ✅ Funciona entre múltiplas instâncias | ✅ Funciona via DB | ❌ Local ao processo |
| **Performance** | ✅ < 1 ms por operação | ⚠ Adiciona latência de transação | ✅ Nanoseconds |
| **Auto-release em crash** | ✅ TTL automático | ✅ Rollback automático | ❌ Lock preso |
| **Sem acoplamento extra** | ✅ Redis já usado | ✅ PostgreSQL já usado | ✅ Sem dependência |
| **Simplicidade de rollback** | ✅ TTL expira | ⚠ Requer transação explícita | ✅ Simples |
| **Escala horizontal do serviço** | ✅ Funciona | ✅ Funciona | ❌ Não funciona |

O Redis já faz parte da infraestrutura (declarado no `docker-compose.yml`), eliminando
dependência adicional.

### Consequências

- **Positivas:** Exclusão mútua garantida sem impacto no esquema do PostgreSQL; TTL previne deadlocks permanentes.
- **Negativas:** Adiciona um hop de rede (Redis) no caminho crítico de reserva; se o Redis estiver indisponível, reservas são bloqueadas.
- **Mitigação de indisponibilidade do Redis:** Falhar fechado (fail closed) — não degradar para `SELECT FOR UPDATE` de forma isolada por instância, pois instâncias sem Redis poderiam reservar o mesmo item que outra com Redis ainda ativo. A consistência é garantida pela condição transacional no PostgreSQL (Sprint 2+).
- **Cuidado de implementação:** A liberação do lock deve verificar que o `request_uuid` ainda é o dono antes de executar `DEL` — usar Lua script atômico:
  ```
  if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
  end
  ```
- **Renovação de lease (Sprint 3):** o TTL curto protege contra crash, mas uma seção crítica mais longa que o TTL expiraria o lease no meio da transação e outra instância entraria na mesma seção. Para fechar essa janela, o `servico-estoque` renova o TTL a cada `TTL/3` com um `PEXPIRE` condicionado ao `request_uuid` do dono e reconfere a posse imediatamente antes do `COMMIT` — se o lease foi perdido, faz rollback e devolve `ESTOQUE_BLOQUEADO`. A garantia de não vender o último item continua sendo do `UPDATE` condicional no PostgreSQL; o lock (com renovação) é o que evita trabalho concorrente e mantém a exclusão mútua durante toda a seção crítica.
- **Detalhamento do modelo de consistência:** invariantes, cenários de falha, o que cada mecanismo garante (e não garante) e limitações em [`docs/consistencia-exclusao-mutua.md`](consistencia-exclusao-mutua.md).

### Alternativas Rejeitadas

- **`SELECT FOR UPDATE` no PostgreSQL:** Viável, mas mantém a transação aberta durante toda a operação, aumentando o tempo de lock no banco e reduzindo throughput sob alta concorrência.
- **Mutex em memória do processo:** Não funciona quando o `servico-estoque` escala horizontalmente para múltiplas réplicas.
- **Redlock (multi-node):** Mais robusto para produção, mas adiciona complexidade desnecessária na Sprint 1 com ambiente single-node.

---

## ADR-005: AWS SQS para Mensageria Assíncrona de Eventos

**Status:** Aceito

### Contexto

Após a criação de um pedido ou atualização de estoque, outros subsistemas futuros
(notificações, analytics, relatórios) precisarão ser informados. Essas operações
**não devem bloquear** o fluxo principal de criação de pedido. Um mecanismo de
desacoplamento assíncrono é necessário.

### Decisão

O sistema utilizará **AWS SQS (Simple Queue Service)** como broker de mensagens para
eventos de domínio não-críticos. Os produtores publicam e esquecem; consumidores
processam em seu próprio ritmo.

Filas definidas na Sprint 1:

| Fila | Tipo | Produtor | Retenção |
|---|---|---|---|
| `caixanamao-pedidos-criados` | Standard | servico-pedidos | 4 dias |
| `caixanamao-estoque-atualizado` | Standard | servico-estoque | 4 dias |

> **Garantia de entrega:** Filas Standard entregam **pelo menos uma vez** e **sem garantia de ordem**.
> Consumidores futuros devem implementar idempotência via `event_id` único para evitar
> processamento duplicado em caso de redelivery.

### Justificativa

| Critério | AWS SQS | Apache Kafka | RabbitMQ |
|---|---|---|---|
| **Operação** | ✅ Gerenciado pela AWS | ❌ Auto-hospedado | ❌ Auto-hospedado |
| **Configuração** | ✅ Mínima | ❌ Cluster complexo | ⚠ Moderada |
| **Custo inicial** | ✅ Free tier generoso | ❌ Infraestrutura própria | ⚠ Container adicional |
| **Durabilidade** | ✅ Replicação automática | ✅ Alta (configurável) | ✅ Alta |
| **Replay de mensagens** | ❌ Sem replay nativo | ✅ Replay por offset | ❌ Sem replay |
| **Throughput** | ✅ Suficiente para o cenário | ✅ Muito alto | ✅ Alto |
| **Integração Python** | ✅ `boto3` (SDK oficial) | ⚠ `confluent-kafka` | ⚠ `pika` |

Para o volume de pedidos de vendedores ambulantes, o throughput do SQS é mais que
suficiente. A ausência de infraestrutura própria para gerenciar reduz significativamente
a carga operacional da equipe nas Sprints iniciais.

### Consequências

- **Positivas:** Sem infraestrutura de broker para operar; falha no SQS não impede criação de pedidos (fire-and-forget); integração via `boto3` com credenciais via variável de ambiente.
- **Negativas:** Sem suporte a replay de eventos (consumidores que ficaram offline perdem mensagens após 4 dias de retenção); lock-in com AWS.
- **Limitação de ambiente local:** Em desenvolvimento local, usar **LocalStack** para emular SQS sem custo e sem acesso à AWS real.
- **Evolução:** Se replay de eventos se tornar requisito (auditoria completa), migrar para Amazon Kinesis ou avaliar Kafka na Sprint 5.

### Alternativas Rejeitadas

- **Apache Kafka:** Replay e alta durabilidade são atraentes, mas exige configuração de cluster Zookeeper/KRaft, aumentando a complexidade operacional além do necessário para Sprint 1.
- **RabbitMQ:** Mais simples que Kafka, mas ainda requer um container adicional e configuração de exchanges/bindings; sem replay.
- **Chamadas REST síncronas para notificar subsistemas:** Cria acoplamento temporal — se o subsistema de notificação estiver indisponível, o pedido falha. Inaceitável.

---

## ADR-006: Padrão UUID para Rastreio Distribuído de Transações e Logs

**Status:** Aceito

### Contexto

Com a execução concorrente de múltiplos terminais móveis e a interação entre microsserviços
via REST (`servico-pedidos`), gRPC (`servico-estoque`) e mensageria assíncrona (`AWS SQS`),
rastrear o fluxo de uma transação distribuída nos logs é desafiador. Quando ocorre uma falha
na reserva, timeout ou compensação, correlacionar os eventos emitidos por diferentes
componentes exige um identificador único transversal que perpasse todas as camadas de
transporte sem poluir os modelos de domínio.

### Decisão

Adotar o padrão de identificação **UUID (RFC 4122 v4)** como rastreador unificado de
transações e requisições distribuídas em todos os microsserviços:

1. **Pares de Rastreio (`correlation_id` e `request_id`):**
   - `correlation_id`: identifica a unidade lógica da transação de negócio de ponta a ponta
     através de fronteiras de serviço (HTTP → gRPC → SQS);
   - `request_id`: identifica a requisição individual (salto de rede) em cada microsserviço.

2. **Propagação na Borda REST (HTTP):**
   - Middleware ASGI `CorrelationMiddleware` em `servico-pedidos` (e health do `servico-estoque`);
   - Captura `X-Correlation-ID` do cliente se enviado (validando se é um UUID válido); caso
     ausente, tenta adotar `X-Request-ID` como correlation id (se for um UUID válido); se o
     valor escolhido estiver malformado, ou nenhum estiver presente, gera um novo UUIDv4 aleatório;
   - Gera (ou preserva, se válido) um `X-Request-ID` para o salto e o ecoa na resposta;
   - Injeta ambos os cabeçalhos nas respostas HTTP e expõe via CORS (`expose_headers`).

3. **Propagação Interna Síncrona (gRPC):**
   - `EstoqueClient` em `servico-pedidos` propaga `x-correlation-id` e `x-request-id` como
     metadados de invocação HTTP/2;
   - `CorrelationServerInterceptor` em `servico-estoque` intercepta chamadas RPC, popula as
     `contextvars` da corrotina correspondente e devolve os mesmos identificadores em
     `trailing_metadata`.

4. **Propagação Assíncrona (AWS SQS):**
   - Produtores SQS incluem `correlation_id` e `request_id` no corpo do payload JSON do
     evento (`PedidoCriado`, `EstoqueAtualizado`) e nos `MessageAttributes` da mensagem
     (permitindo filtragem sem deserialização).

5. **Logs Contextualizados e Unificados:**
   - Variáveis assíncronas `contextvars.ContextVar` isoladas por corrotina;
   - `CorrelationLogFilter` injeta `correlation_id` e `request_id` no `LogRecord` de todos os
     loggers da aplicação;
   - Formato padrão unificado:
     `%(levelname)s | %(name)s | [corr=%(correlation_id)s req=%(request_id)s] | %(message)s`.

### Justificativa

| Critério | UUID (ContextVars + Headers) | OpenTelemetry / Jaeger SDK | Parâmetro explícito em métodos |
|---|---|---|---|
| **Overhead de CPU/RAM** | ✅ Desprezível (< 10 µs) | ⚠ Elevado em t3.micro | ✅ Baixo |
| **Complexidade operacional** | ✅ Zero infra extra | ❌ Exige collector/daemon | ✅ Zero infra |
| **Acoplamento de código** | ✅ Transparente (middleware/interceptor) | ⚠ Alto (instrumentação pesada) | ❌ Invasivo em todo método |
| **Integração multi-transporte**| ✅ HTTP, gRPC e SQS unificados | ⚠ Requer plugins específicos | ❌ Manual em cada payload |
| **Compatibilidade de logs** | ✅ Qualquer agregador (CloudWatch/Loki) | ✅ Padrão OpenTelemetry | ⚠ Inconsistente |

### Consequências

- **Positivas:** Logs agregados podem ser filtrados instantaneamente por `corr=<uuid>` para
  reconstruir toda a jornada do pedido (HTTP → gRPC → Banco → SQS); diagnóstico de
  falhas concorrentes simplificado; zero alteração estrutural no contrato `.proto`.
- **Negativas:** Requer disciplina na criação de threads ou tarefas em background para não
  perder o contexto (mitigado pelo uso de `asyncio.to_thread` e passagem explícita quando
  necessário).
- **Validação:** Cobertura de testes unitários e de integração validando round-trip de
  headers HTTP, trailing metadata gRPC e atributos SQS.

### Alternativas Rejeitadas

- **Integração com OpenTelemetry / Jaeger:** Muito poderosa, mas adiciona overhead de
  dependências pesadas e consumo de memória que violariam a restrição de execução da
  instância AWS `t3.micro` (1 GB RAM com swap) já estabelecida para o projeto.
- **IDs sequenciais / timestamps:** Inviáveis para sistemas distribuídos com múltiplos
  nós independentes sem um serviço central de coordenação (risco de colisão).

---

## ADR-007: Replicação Streaming Assíncrona com Hot Standby e Promoção Manual

**Status:** Aceito
**Sprint:** 3 — issue #11

### Contexto

O ADR-003 concentra os três databases lógicos em um único cluster PostgreSQL 16. A decisão
reduziu o custo operacional, mas deixou explícito o trade-off: o cluster é **ponto único de
falha** para todos os serviços. A Sprint 3 exige replicação master/slave para aumentar a
disponibilidade e preservar a consistência dos dados.

O ambiente de referência é uma instância `t3.micro` (1 GB de RAM, com swap) no AWS Academy
Sandbox, que é destruída ao fim de cada sessão. Qualquer solução precisa ser reproduzível via
Docker Compose, não pode depender de infraestrutura gerenciada e deve caber nessa restrição.

### Decisão

Adotar **replicação física por streaming** entre dois containers da mesma imagem
`postgres:16`:

- **`postgres`** — primário; recebe todas as escritas; `wal_level=replica`,
  `max_wal_senders=10`, `max_replication_slots=10`, `wal_keep_size=512MB`,
  `max_slot_wal_keep_size=2GB`, `hot_standby=on`.
- **`postgres-replica`** — hot standby em recovery contínua; `pg_basebackup` inicial com
  **slot físico** `caixanamao_replica`; `hot_standby_feedback=on`; somente leitura.
- **`replicacao-init`** — serviço one-shot idempotente que cria o papel `replicator`
  (`LOGIN REPLICATION`, senha em `REPLICATION_PASSWORD`) e o slot físico. Roda a cada
  `docker compose up` e torna o setup reproduzível em volumes novos **e antigos**.
- **Autenticação** — apenas SCRAM-SHA-256; a regra de `pg_hba` para a rede do Compose é
  garantida pelo entrypoint do primário, porque `pg_hba.conf` não é editável por SQL.

A replicação é **assíncrona** e o failover é **manual**. Os microsserviços continuam
apontando para o primário — nenhuma string de conexão mudou nesta issue.

### Justificativa

| Critério | Streaming assíncrona (escolhida) | Streaming síncrona | Replicação lógica |
|---|---|---|---|
| **Complexidade operacional** | ✅ Baixa (2 containers, 1 imagem) | ⚠ Média (gerenciar quórum de standby) | ⚠ Alta (publicações por tabela, schema não replicado) |
| **Disponibilidade de escrita** | ✅ `COMMIT` não depende da réplica | ❌ Réplica fora ⇒ escrita bloqueia/falha | ✅ Igual à assíncrona |
| **RPO** | ⚠ > 0 (janela de WAL não enviado) | ✅ 0 (confirmação no standby) | ⚠ > 0 |
| **Consistência da cópia** | ✅ Física, bloco a bloco | ✅ Física | ⚠ Só tabelas publicadas |
| **Cabe em `t3.micro`** | ✅ ~2 processos PostgreSQL | ✅ Idem, porém mais sensível a latência | ⚠ Overhead de decodificação |
| **Pronto para failover** | ✅ Basta promover | ✅ Basta promover | ⚠ Exige repontar assinaturas/consumidores |

A consistência forte do estoque (ADR-004) é preservada porque **toda escrita continua no
primário**: as transações `SELECT ... FOR UPDATE`/`UPDATE` condicional e o lock Redis não
mudam de lugar. A réplica é uma cópia para leitura e continuidade, não um segundo primário.

### Consequências

- **Positivas:** elimina a dependência de um único processo PostgreSQL para leitura e
  diagnóstico; fornece standby atualizado para promoção manual; setup 100 % Docker Compose,
  reproduzível em qualquer máquina; validação automatizada em
  `scripts/validar_replicacao.sh` (7 verificações, incluindo escrita replicada e réplica
  read-only).
- **Negativas:** RPO maior que zero (replicação assíncrona); RTO depende de operação manual;
  WAL pode ocupar até 2 GB no primário se a réplica ficar parada; re-clone necessário se o
  slot for invalidado; a réplica não é usada pelos serviços nesta issue, então não há ganho
  de throughput de leitura na aplicação ainda.
- **Cuidados:** não iniciar o primário antigo após uma promoção (split-brain); replicação
  física **não substitui backup**; atualizar `REPLICATION_PASSWORD` em `.env` (o
  `deploy.sh` já gera um segredo aleatório ao criar o arquivo).

### Alternativas Rejeitadas

- **Replicação síncrona como default:** daria RPO zero, mas acopla a disponibilidade de
  escrita à réplica. Com a réplica fora do ar, os `COMMIT` do primário bloqueiam ou falham
  — inaceitável para a stack de desenvolvimento, que deve subir só com o primário, e não
  resolve o ponto de falha, apenas o desloca. Fica registrada como evolução para produção.
- **Patroni/repmgr (failover automático):** exigiria consenso, quórum e um serviço extra de
  coordenação; complexidade e consumo de memória desproporcionais à instância `t3.micro` e
  ao escopo da Sprint 3.
- **Replicação lógica:** replicaria apenas tabelas publicadas, exigiria gerenciar
  publicações/assinaturas e não replicaria roles/schema; não entrega um standby pronto para
  assumir o cluster inteiro.
- **Serviços de banco gerenciado (RDS):** eliminariam a operação, mas contrariam o objetivo
  didático de configurar a replicação e o requisito de rodar no sandbox sem custo.
