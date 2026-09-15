# Decisões Técnicas — Architecture Decision Records (ADRs)

> **Projeto:** CaixaNaMao  
> **Sprint:** 1  
> **Autor:** c4rlosfb  
> **Status:** Aprovado pela equipe  
> **Última atualização:** 2026-09-15

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

Toda comunicação **serviço-a-serviço** utilizará **gRPC** com contratos definidos em
Protocol Buffers (`.proto`) centralizados na pasta `shared-protos/`.

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
SETNX lock:estoque:{item_id}  {request_uuid}  EX 5
```

- `NX` garante que apenas uma requisição adquira o lock por vez
- `EX 5` garante que o lock expire automaticamente em caso de crash do serviço
- O valor `{request_uuid}` garante que apenas o dono do lock possa liberá-lo (evita liberação acidental por outro processo)

### Justificativa

| Critério | Redis SETNX | PostgreSQL `SELECT FOR UPDATE` | Mutex em memória |
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
- **Mitigação de indisponibilidade do Redis:** Circuit Breaker (Sprint 4) detectará falha e poderá degradar para `SELECT FOR UPDATE` no PostgreSQL como fallback.
- **Cuidado de implementação:** A liberação do lock deve verificar que o `request_uuid` ainda é o dono antes de executar `DEL` (usar Lua script atômico).

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
