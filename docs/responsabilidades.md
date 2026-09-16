# Distribuição de Responsabilidades e Justificativas Técnicas

> **Projeto:** CaixaNaMao  
> **Sprint:** 1  
> **Autor:** EduardoTenorioNunes (Eduardo Tenório Nunes)  
> **Status:** Em revisão  
> **Última atualização:** 2026-09-16

---

## 1. Objetivo

Este documento atende à issue **#4 — [Issue 3] Distribuição de responsabilidades e justificativas técnicas** (Sprint 1) em duas partes:

1. **Distribuição de responsabilidades**: quem responde por cada área do projeto e como as entregas estão atribuídas no board (milestones e issues do GitHub);
2. **Justificativas técnicas**: consolidação do racional das escolhas arquiteturais — os ADRs (`docs/decisoes-tecnicas.md`, Issue #2, em revisão no PR #23) permanecem a fonte canônica, e as decisões não cobertas por eles são complementadas aqui.

---

## 2. Equipe

| Membro | Papel principal | Foco principal |
| --- | --- | --- |
| **c4rlosfb** | Tech Lead / Arquitetura e Backend | Serviços de Pedidos e Estoque, contratos gRPC, padrões transversais (UUID, Circuit Breaker, notificações SQS) |
| **LuanCasDias** | Infraestrutura e Operação | Docker/docker-compose, AWS (deploy, API Gateway, Auto Scaling), validação de ambiente |
| **EduardoTenorioNunes** | Documentação Técnica, Dados e Segurança | Documentos e diagramas, PostgreSQL com replicação, Redis Lock, JWT e TLS |

A atribuição por área segue o board do projeto; o balanceamento é revisitado na planning de cada sprint e mudanças são registradas nas issues.

---

## 3. Distribuição de Responsabilidades

### 3.1 Por área técnica

| Área | Responsável principal | Revisão padrão |
| --- | --- | --- |
| Arquitetura e ADRs | c4rlosfb | EduardoTenorioNunes |
| Serviços de Pedidos e Estoque (REST + gRPC) e contratos `.proto` | c4rlosfb | EduardoTenorioNunes |
| Infraestrutura local (Docker/compose) e AWS | LuanCasDias | c4rlosfb |
| Banco de dados e consistência (PostgreSQL, replicação, Redis Lock) | EduardoTenorioNunes | c4rlosfb |
| Autenticação (JWT) e segurança (TLS) | EduardoTenorioNunes | c4rlosfb |
| Documentação técnica e diagramas | EduardoTenorioNunes | c4rlosfb |
| Serviço de Notificações (SQS) | c4rlosfb | EduardoTenorioNunes |
| Demonstração final e integração | Todos | — |

### 3.2 Por sprint (rastreabilidade com o board)

| Sprint | Issue | Entrega | Responsável |
| --- | --- | --- | --- |
| 1 | #2 | Validação da estrutura do repositório | LuanCasDias |
| 1 | #3 | Documento de definição e planejamento da arquitetura | c4rlosfb |
| 1 | #4 | **Este documento** (responsabilidades + justificativas) | EduardoTenorioNunes |
| 2 | #5 | Serviço de Pedidos com endpoints REST | c4rlosfb |
| 2 | #6 | Serviço de Estoque e servidor gRPC | c4rlosfb |
| 2 | #7 | Contrato `.proto` e integração gRPC Pedidos ↔ Estoque | c4rlosfb |
| 2 | #8 | Ambiente AWS e deploy inicial de container | LuanCasDias |
| 2 | #9 | Documentação das comunicações REST e gRPC | EduardoTenorioNunes |
| 3 | #10 | Padrão UUID para rastreio de transações | c4rlosfb |
| 3 | #11 | PostgreSQL com replicação Master/Slave | EduardoTenorioNunes |
| 3 | #12 | Exclusão mútua distribuída com Redis Lock | EduardoTenorioNunes |
| 3 | #13 | Documentação do modelo de consistência e exclusão mútua | EduardoTenorioNunes |
| 4 | #14 | Serviço de Autenticação com JWT | EduardoTenorioNunes |
| 4 | #15 | AWS API Gateway para roteamento externo | LuanCasDias |
| 4 | #16 | Circuit Breaker nos microsserviços | c4rlosfb |
| 4 | #17 | Auto Scaling e monitoramento (CloudWatch) | LuanCasDias |
| 4 | #18 | Criptografia TLS na comunicação externa | EduardoTenorioNunes |
| 5 | #19 | Serviço de Notificações com AWS SQS | c4rlosfb |
| 5 | #20 | Diagrama de arquitetura e relatório técnico final | EduardoTenorioNunes |
| 5 | #21 | Revisão de código e validação da orquestração Docker | LuanCasDias |
| 5 | #22 | Ambiente e roteiro da demonstração prática | Todos |

### 3.3 Fluxo de colaboração

- **Uma branch por issue** (padrão `docs/issue-<n>-<tema>` ou `fix/issue-<n>-<tema>`), integrada via Pull Request para a branch `dev`;
- **Revisão cruzada**: todo PR precisa de aprovação de pelo menos um membro que não seja o autor (pares sugeridos na seção 3.1);
- **Commits semânticos** (`docs:`, `feat:`, `fix:`) sempre referenciando a issue (`closes #N`);
- **Definição de pronto (DoD)**:
  - *Código*: lint e testes passando, serviço funcionando via `docker-compose` e PR aprovado;
  - *Documentação*: conteúdo revisado por outro membro, markdownlint sem erros e links válidos.

---

## 4. Justificativas Técnicas das Escolhas Arquiteturais

As decisões estruturais estão registradas nos **ADRs** (`docs/decisoes-tecnicas.md` — Issue #2); a tabela abaixo consolida o racional de cada uma e as escolhas complementares vêm na sequência.

### 4.1 Decisões registradas em ADR

| Decisão | Justificativa central | ADR |
| --- | --- | --- |
| REST externo + gRPC interno | API de borda com compatibilidade universal e depuração simples; chamada crítica Pedidos → Estoque com contrato tipado e baixa latência (HTTP/2 + Protobuf) sob concorrência | ADR-001, ADR-002 |
| Cluster único PostgreSQL com databases lógicos | Isolamento lógico (um database e credenciais por serviço) com custo operacional de um único container; replicação física futura sem mudança de código | ADR-003 |
| Redis como lock distribuído na reserva | Exclusão mútua entre instâncias com TTL (auto-liberação em caso de crash) e latência abaixo de 1 ms no caminho crítico | ADR-004 |
| AWS SQS para eventos assíncronos | Desacoplamento dos fluxos não críticos sem operar broker próprio; integração direta via `boto3` e emulação local com LocalStack | ADR-005 |

### 4.2 Escolhas complementares (não cobertas por ADR)

- **Três microsserviços** (autenticação, pedidos e estoque): fronteiras por domínio de negócio que cobrem todos os padrões exigidos pelo trabalho — REST, gRPC, mensageria assíncrona e consistência distribuída — sem over-engineering;
- **Python 3.12 + FastAPI + grpcio**: produtividade da equipe e ecossistema maduro para tudo que o projeto precisa (`boto3`, SQLAlchemy/Alembic, `redis-py`), com documentação OpenAPI gerada automaticamente e suporte nativo a gRPC;
- **PostgreSQL 16**: transações ACID necessárias à consistência forte do estoque, replicação física simples planejada para a Sprint 3 e suporte nativo a UUID;
- **Docker e docker-compose**: ambiente local reproduzível e caminho direto de migração para containers na AWS;
- **Divisão do trabalho por área** (backend, infraestrutura, documentação + dados): maximiza o paralelismo — infraestrutura e documentação não bloqueiam o caminho crítico do backend (Sprint 2) — e garante revisão cruzada entre áreas distintas.

---

## 5. Referências

- [`docs/arquitetura.md`](./arquitetura.md) — Definição e planejamento da arquitetura (Issue #2);
- [`docs/decisoes-tecnicas.md`](./decisoes-tecnicas.md) — Architecture Decision Records (Issue #2);
- Board do projeto — milestones Sprint 1–5 com as respectivas issues e responsáveis.

> **Nota:** os dois documentos acima são entregues pelo PR #23 (Issue #2), em revisão; os links passam a resolver após o merge.
