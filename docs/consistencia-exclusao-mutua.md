# Modelo de Consistência e Exclusão Mútua Distribuída

> **Projeto:** CaixaNaMao  
> **Sprint:** 3  
> **Issue:** #12 — "Documentar modelo de consistência e algoritmo de exclusão mútua" (GitHub #13)  
> **Autor:** EduardoTenorioNunes  
> **Status:** Entregue  
> **Última atualização:** 2026-10-26

---

## 1. Objetivo e escopo

Este documento responde a duas perguntas com lastro no código que está no repositório:

1. **Qual modelo de consistência** o CaixaNaMão adota — internamente, entre serviços e em
   relação à réplica do PostgreSQL?
2. **Como funciona o algoritmo de exclusão mútua** (Redis Lock) que protege a reserva de
   estoque, incluindo renovação de lease, reconferência de posse e os cenários de falha?

O caminho crítico analisado é o par `CheckAndReserve` / `ReleaseReserva` do
`servico-estoque`, implementado em
[`servico-estoque/app/services/estoque_service.py`](../servico-estoque/app/services/estoque_service.py)
e
[`servico-estoque/app/locking/redis_lock.py`](../servico-estoque/app/locking/redis_lock.py),
com a escrita atômica em
[`servico-estoque/app/repositories/estoque_repository.py`](../servico-estoque/app/repositories/estoque_repository.py).

**Fora de escopo:** a configuração e o runbook da replicação
([`replicacao-postgres.md`](replicacao-postgres.md)), o contrato REST/gRPC completo
([`comunicacoes-rest-grpc.md`](comunicacoes-rest-grpc.md)) e a decisão arquitetural em si
([ADR-004](decisoes-tecnicas.md#adr-004-redis-para-distributed-locking-na-reserva-de-estoque)).
Aqui está o detalhamento operacional do modelo — inclusive do que ele **não** garante.

---

## 2. O problema: a corrida do último item

Dois terminais consultam o mesmo item, que tem **1 unidade** disponível. Sem coordenação, a
sequência clássica de leitura-modificação-escrita permite que ambos confirmem a venda:

```
Terminal A                          Terminal B
    |  SELECT quantidade -> 1           |
    |                                   |  SELECT quantidade -> 1
    |  UPDATE ... SET quantidade = 0    |
    |                                   |  UPDATE ... SET quantidade = 0
    |  COMMIT  (vendeu)                 |  COMMIT  (vendeu de novo)
```

O `SELECT` não bloqueia nada, e a segunda escrita sobrescreve a primeira: **duas vendas do
mesmo item**. O CaixaNaMão fecha essa janela em duas camadas independentes:

- **camada de contenção** — um lock distribuído no Redis evita que duas requisições executem
  a seção crítica do mesmo item ao mesmo tempo, e devolve `ESTOQUE_BLOQUEADO` cedo a quem
  perder a disputa;
- **camada de correção** — o débito é um `UPDATE` **condicional**, avaliado pelo PostgreSQL;
  mesmo que o lock seja perdido no meio da operação, duas transações concorrentes não
  conseguem debitar o mesmo saldo.

Essa separação é o ponto central do modelo e está registrada em comentário no início do
repositório de estoque:

> a garantia de "nunca vender o último item duas vezes" **não** vem do TTL do lock do Redis,
> e sim da condição transacional avaliada pelo PostgreSQL na própria escrita.

---

## 3. Modelo de consistência adotado

### 3.1 Em uma frase

**Consistência forte dentro do `estoque_db`**, com fronteiras explícitas e declaradas:
consistência **eventual com compensação** entre `servico-pedidos` e `servico-estoque`,
consistência **eventual best-effort** para os eventos assíncronos e consistência **eventual
por replicação assíncrona** para a réplica de leitura — que os serviços não usam.

Não existe transação distribuída (2PC/XA) entre os três databases. O invariante de negócio
— *não vender duas vezes o último item* — é garantido inteiramente dentro do `estoque_db`,
o que dispensa coordenação transacional entre serviços.

### 3.2 Classificação por fronteira

| Fronteira | Modelo | Garantia concreta | Onde está implementado |
|---|---|---|---|
| Saldo em `itens` (reserva/liberação) | **Forte** — transação ACID, isolamento `READ COMMITTED` do PostgreSQL, com predicado reavaliado pelo próprio `UPDATE` | Duas reservas concorrentes do mesmo item não debitam o mesmo saldo; `quantidade_disponivel` nunca fica negativa | `reservar_atomico` / `liberar_atomico` + `CHECK` constraints |
| Exclusão mútua por item | **Linearizável por chave** — no máximo uma dona por `lock:estoque:{item_id}` | Uma única seção crítica por item por vez; as demais recebem `ESTOQUE_BLOQUEADO` | `RedisLockManager` |
| Ledger `movimentacoes` | **Idempotente** por `(pedido_id, item_id, tipo)` | Retry do mesmo item não debita nem devolve duas vezes | `UNIQUE uq_movimentacoes_pedido_item_tipo` + tratamento de `IntegrityError` |
| Pedido ↔ reserva (entre serviços) | **Eventual, com compensação** — saga coreografada | Não há venda dupla; existe uma janela em que o estoque está reservado e o pedido ainda não foi persistido | `servico-pedidos` chamando `ReleaseReserva` ([detalhes](comunicacoes-rest-grpc.md#35-compensação-distribuída)) |
| Leitura de item (`ConsultarItem`) | **Read committed**, sem snapshot entre itens | O valor lido é sempre de uma transação commitada; não há leitura suja | `EstoqueService.consultar` |
| Eventos `EstoqueAtualizado` (SQS) | **Eventual, best-effort, at-least-once** | O evento pode ser perdido; consumidores deduplicam por `event_id` | `SQSEventPublisher` |
| Réplica PostgreSQL | **Eventual** — streaming assíncrona, RPO > 0 | Cópia física atualizada com atraso; **não** é usada no caminho de leitura dos serviços | [ADR-007](decisoes-tecnicas.md#adr-007-replicação-streaming-assíncrona-com-hot-standby-e-promoção-manual) / [`replicacao-postgres.md`](replicacao-postgres.md) |

### 3.3 Por que consistência forte apenas onde importa

| Alternativa | Por que não |
|---|---|
| Transação distribuída (2PC/XA) entre `pedidos_db` e `estoque_db` | Acoplaria a disponibilidade dos dois bancos a um coordenador; com o serviço de estoque ou o coordenador fora, o pedido inteiro travaria. O invariante que realmente importa cabe em um único database. |
| Consistência eventual também no saldo | Inaceitável para o requisito do trabalho: o saldo é justamente o dado que não pode divergir. |
| Roteamento de leitura para a réplica | A réplica é assíncrona; uma leitura após escrita poderia devolver saldo antigo. Fora de escopo nesta Sprint e explicitamente não adotado (ADR-007). |

O custo aceito é o estado intermediário da saga: uma reserva confirmada cujo pedido não
chegou a ser persistido permanece no `estoque_db` até a compensação. É um estado
**observável e rastreável** — cada liberação compensatória falha de forma isolada e é logada
com `pedido_id` e `item_id` — mas não é um estado atômico entre os dois serviços.

### 3.4 Invariantes garantidos

| ID | Invariante | Mecanismo principal | Defesa em profundidade |
|---|---|---|---|
| **INV-1** | `quantidade_disponivel >= 0` e `quantidade_reservada >= 0` | Predicado `WHERE quantidade_disponivel >= :q` no `UPDATE` | `CHECK ck_itens_disponivel_nao_negativo` e `ck_itens_reservada_nao_negativa` |
| **INV-2** | Reserva e liberação movem o saldo nas duas colunas **na mesma transação**: `disponivel -= q` e `reservada += q` (e o inverso na liberação) | `UPDATE` único com `RETURNING` + `COMMIT` conjunto com o ledger | `UPDATE` e `INSERT` só persistem juntos no `COMMIT`; qualquer exceção leva a `rollback` |
| **INV-3** | No máximo uma movimentação `RESERVA` e uma `LIBERACAO` por `(pedido_id, item_id)` | `UNIQUE (pedido_id, item_id, tipo)` | `IntegrityError` é tratado: a transação é revertida e o resultado é reconferido antes de responder `CONFIRMADO` |
| **INV-4** | O último item não é vendido duas vezes | **INV-1 + INV-3** | O lock reduz a contenção e acelera a recusa, mas **não** é o que sustenta esse invariante |
| **INV-5** | No máximo uma dona do lock por `item_id` | `SET ... NX` — operação atômica no servidor Redis | Valor `request_uuid` impede liberação por terceiros |

> **Leitura importante:** o lock **não** aparece como mecanismo principal de INV-1 nem
> INV-4. Ele é uma otimização de contenção e previsibilidade — quem não é dono recebe
> `ESTOQUE_BLOQUEADO` sem abrir transação no PostgreSQL. A prova formal do invariante está
> no banco, e por isso ela sobrevive a um lock expirado, a um Redis reiniciado ou a uma
> partição de rede.

---

## 4. Algoritmo de exclusão mútua (Redis Lock)

### 4.1 Estrutura do lock

| Elemento | Valor | Por quê |
|---|---|---|
| **Chave** | `lock:estoque:{item_id}` | Granularidade por item: reservas de itens diferentes correm em paralelo, sem serializar o serviço inteiro |
| **Valor** | `request_uuid` da operação | Token de propriedade — permite que só o dono libere e renove (o lock não é um booleano) |
| **TTL** | `LOCK_TTL_SECONDS`, padrão **5 s** (`Settings.lock_ttl_seconds`) | Se o processo morrer com o lock adquirido, a chave desaparece sozinha e o item não fica travado para sempre |
| **Prefixo** | `lock:estoque` (configurável no construtor) | Isola o namespace do lock de outras chaves que venham a usar o mesmo Redis |
| **Validação** | TTL precisa ser `int > 0` | `bool` é subclasse de `int` em Python e é recusado junto com valores inválidos — um TTL inválido não pode virar `1` silenciosamente |

### 4.2 Ciclo de vida

```mermaid
stateDiagram-v2
    [*] --> Livre
    Livre --> Adquirido: SET NX EX devolve OK
    Livre --> Recusado: chave ja existe, outra requisicao e dona
    Adquirido --> Adquirido: watchdog renova o TTL a cada TTL/3
    Adquirido --> Liberado: Lua compare-and-delete
    Adquirido --> Expirado: TTL venceu sem renovacao
    Recusado --> [*]
    Liberado --> [*]
    Expirado --> [*]
```

### 4.3 Aquisição — `SET ... NX EX`

A aquisição é **um único comando**, atômico no servidor:

```python
resultado = await self._redis.set(chave, str(request_uuid), nx=True, ex=self._ttl)
```

- `NX` — só grava se a chave **não existir**: exatamente uma requisição ganha a disputa.
- `EX` — define o TTL na mesma operação.

`SETNX` seguido de `EXPIRE` seria uma armadilha: se o processo caísse entre os dois
comandos, ficaria um lock **sem TTL** — imortal — e o item ficaria bloqueado até intervenção
manual. Com `SET ... NX EX` o TTL é parte da mesma operação atômica.

Do ponto de vista do chamador:

| Resultado | Significado | Resposta do serviço |
|---|---|---|
| `True` | Lock adquirido, podemos entrar na seção crítica | Prossegue para a transação |
| `False` | Outra requisição é dona no momento | `ESTOQUE_BLOQUEADO` com mensagem de retry, **sem tocar no banco** |
| `LockIndisponivelError` | Redis não respondeu ou a conexão falhou | `ESTOQUE_BLOQUEADO` — **fail closed** |

### 4.4 Renovação de lease — o watchdog

O TTL curto é o que protege contra crash, mas cria um problema simétrico: uma operação
**mais lenta que o TTL** veria o lease expirar no meio da transação, e outra instância
entraria na mesma seção crítica. Para fechar essa janela, o serviço mantém o lease vivo
enquanto o bloco crítico roda:

- o watchdog acorda a cada **`TTL / 3`** (≈ 1,67 s com o padrão de 5 s; piso de 50 ms para
  TTLs muito baixos não virarem busy loop). A folga de 1/3 permite **duas** renovações
  seguidas falharem sem que o lease expire, absorvendo jitter do event loop e latência
  pontual do Redis;
- a renovação também é atômica e **condicionada ao dono**, via script Lua:

```lua
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
```

  Se a chave já pertence a outra requisição, o script devolve `0` e **não** estende o TTL de
  terceiros.
- o watchdog é uma tarefa `asyncio` cancelada na saída do bloco `async with` — inclusive
  quando a seção crítica levanta exceção. Uma falha do watchdog **nunca substitui** a
  exceção original da seção crítica (por exemplo, um `IntegrityError` que depende do
  `rollback` feito pelo chamador): ela é logada e o fluxo segue;
- se a renovação devolver `False` (lease perdido) ou o Redis falhar, o watchdog registra o
  erro e **encerra**. Ele não derruba a operação em andamento — a decisão final é da
  reconferência de posse antes do `COMMIT`.

### 4.5 Reconferência de posse antes do `COMMIT`

Entre a última renovação e o `COMMIT` existe um intervalo. Para não commitar às cegas, o
serviço consulta o lock imediatamente antes:

```python
if not await self._posse_confirmada(item_id, request_uuid):
    precisa_liberar_lock = False
    await self._session.rollback()
    return ResultadoReserva(StatusReserva.ESTOQUE_BLOQUEADO, ...)
```

- a checagem é `GET lock:estoque:{item_id} == request_uuid`, **sem** alterar o TTL;
- **fail closed:** se o Redis não responder na checagem, o resultado é `False` e a transação
  é descartada. É preferível recusar uma reserva a commitá-la sem saber se a exclusão mútua
  ainda valia;
- quando a posse já foi perdida, o `finally` **não** tenta liberar o lock (`precisa_liberar_lock
  = False`): o lock não é mais nosso, e um `EVAL` adicional só geraria um warning enganoso.

### 4.6 Liberação — compare-and-delete

Um `DEL` simples seria incorreto: se o nosso lease expirou e outra requisição adquiriu o
lock, o `DEL` apagaria o lock **de outra pessoa**, permitindo que duas seções críticas
corressem em paralelo. A liberação usa um script Lua, atômico:

```lua
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
```

| Retorno | Interpretação | Ação |
|---|---|---|
| `True` | O lock era nosso e foi removido | Nada a fazer |
| `False` | O lease já tinha expirado ou o lock é de outra requisição | `WARNING` — a operação **já foi commitada**; o TTL de quem pegou o lock cuida do resto |
| `LockIndisponivelError` | Redis caiu na liberação | `WARNING`; a operação commitada continua válida e o TTL expira a chave |

A liberação acontece no `finally`, depois do `COMMIT`: a falha em liberar o lock **nunca**
invalida uma operação já commitada.

### 4.7 Fail closed: indisponibilidade do Redis é indisponibilidade de reserva

Se o Redis não responde, o `servico-estoque` **recusa** a reserva e a liberação com
`ESTOQUE_BLOQUEADO`, sem abrir transação. A alternativa tentadora — degradar para
`SELECT FOR UPDATE` quando o Redis falha — é explicitamente rejeitada no ADR-004: uma
instância sem Redis poderia reservar o mesmo item que outra instância ainda com o Redis
ativo, e a exclusão mútua deixaria de existir justamente no momento de falha parcial.

Esse é um trade-off deliberado de **consistência sobre disponibilidade** no caminho crítico:
o sistema prefere não vender a vender duas vezes.

### 4.8 Sequência completa

```mermaid
sequenceDiagram
    autonumber
    participant P as servico-pedidos
    participant E as servico-estoque
    participant W as watchdog
    participant R as Redis
    participant DB as estoque_db

    P->>E: CheckAndReserve item_id, quantidade, pedido_id, request_uuid
    E->>DB: busca movimentacao RESERVA do par pedido_id + item_id
    alt reserva ja registrada
        E-->>P: CONFIRMADO idempotente
    else primeira tentativa
        E->>R: SET lock:estoque:item_id request_uuid NX EX 5
        alt lock ocupado ou Redis fora
            E-->>P: ESTOQUE_BLOQUEADO
        else lock adquirido
            E->>W: manter_vivo inicia tarefa de renovacao
            loop a cada TTL/3
                W->>R: EVAL PEXPIRE condicionado ao request_uuid
            end
            E->>DB: UPDATE itens WHERE id e quantidade_disponivel >= q RETURNING
            alt linha afetada
                E->>DB: INSERT movimentacoes RESERVA
                E->>R: GET lock == request_uuid
                alt posse confirmada
                    E->>DB: COMMIT
                    E->>R: EVAL compare-and-delete
                    E-->>P: CONFIRMADO
                else lease perdido ou Redis fora na checagem
                    E->>DB: ROLLBACK
                    E-->>P: ESTOQUE_BLOQUEADO
                end
            else nenhuma linha afetada
                E->>DB: ROLLBACK
                E-->>P: ESTOQUE_INSUFICIENTE ou NOT_FOUND
            end
            E->>W: cancelamento do watchdog no fim do bloco
        end
    end
```

---

## 5. Quem garante o quê

A tabela abaixo é a versão curta do que cada mecanismo cobre — e do que ele **não** cobre.
Ela existe para evitar a leitura errada mais comum: supor que o lock sozinho impede venda
dupla.

| Mecanismo | Garante | **Não** garante |
|---|---|---|
| Redis Lock (`SET NX EX`) | No máximo uma seção crítica por `item_id`; recusa rápida sob contenção | Que o saldo não fique negativo; que o dono ainda seja dono no `COMMIT` |
| Renovação de lease | Que uma seção crítica mais longa que o TTL não perca o lock | Que o processo esteja vivo ou que a renovação seja pontual |
| Reconferência pré-`COMMIT` | Descarta a transação quando a posse já se perdeu | Eliminar a janela entre a checagem e o `COMMIT` |
| `UPDATE` condicional | Invariante de saldo; a garantia de não vender o último item | Idempotência por pedido |
| `UNIQUE (pedido_id, item_id, tipo)` | Idempotência do ledger | Que a quantidade informada seja igual à da reserva anterior — isso é tratado no domínio, com `CONFLITO_IDEMPOTENCIA` |
| Compensação no `servico-pedidos` | Desfaz reservas parciais quando o pedido falha | Atomicidade entre os bancos; a compensação é best-effort e pode falhar |

---

## 6. Cenários de falha

| Cenário | O que acontece | Por que o invariante se mantém | Cobertura |
|---|---|---|---|
| **Serviço cai com o lock adquirido** | A transação não commitada é descartada pelo PostgreSQL; a chave expira em ≤ `LOCK_TTL_SECONDS` | O saldo só muda no `COMMIT`; nenhum estado persistido depende do lock | Garantido pelo TTL do lado do servidor Redis; **não há teste de caos** para queda de processo |
| **Transação mais lenta que o TTL** | O watchdog renova o lease a cada `TTL/3` e a posse é reconferida antes do `COMMIT` | O lease não expira durante a operação | `test_lease_renovado_mantem_a_exclusao_mutua_em_reserva_lenta`, `test_manter_vivo_renova_o_lease_alem_do_ttl` |
| **Event loop travado ou lease perdido antes do `COMMIT`** | A checagem de posse falha; a transação é revertida e a resposta é `ESTOQUE_BLOQUEADO` | Nada foi commitado; a próxima dona encontra o saldo intacto | `test_commit_e_descartado_quando_o_lease_e_perdido` |
| **Lease perdido durante a liberação** | A transação é revertida e não há tentativa de `DEL` | Mesmo raciocínio do caso anterior, no caminho espelhado | `test_liberacao_e_descartada_quando_o_lease_e_perdido`, `test_lease_perdido_nao_dispara_liberacao_redundante` |
| **Lock já pertence a outra requisição na liberação** | O `EVAL` devolve `0` e o `DEL` não acontece | Ninguém apaga lock de terceiros; a exclusão mútua da nova dona é preservada | `test_liberacao_so_remove_lock_do_proprio_dono` |
| **Redis reinicia entre o lock e o `COMMIT`** | A checagem de posse levanta `LockIndisponivelError`, é tratada como `False` e a reserva é recusada | Fail closed: não se commita sem confirmação de posse | `test_redis_cai_entre_o_lock_e_o_commit_e_a_reserva_e_recusada` |
| **Redis indisponível na aquisição** | `LockIndisponivelError` → `ESTOQUE_BLOQUEADO`, sem tocar no banco | Fail closed | `test_redis_indisponivel_falha_fechado` e, no PostgreSQL real, `test_redis_indisponivel_falha_fechado_sem_tocar_no_banco` |
| **Retry do mesmo item do mesmo pedido** | A reserva existente é encontrada antes do lock e a resposta é `CONFIRMADO` idempotente | `UNIQUE` + leitura prévia; corrida residual cai no `IntegrityError` e é reconferida | `test_retry_do_mesmo_pedido_nao_debita_duas_vezes`, `test_retry_do_mesmo_pedido_com_parametros_diferentes_e_conflito` |
| **Duas instâncias disputando o último item** | Uma adquire o lock e reserva; a outra recebe `ESTOQUE_BLOQUEADO` | `SET NX` + `UPDATE` condicional | `test_ultimo_item_nao_pode_ser_reservado_duas_vezes`, `test_apenas_uma_entre_varias_instancias_adquire_o_mesmo_item` |
| **Duas instâncias com o lock ineficaz, no PostgreSQL real** | Ainda assim apenas **uma** reserva é confirmada | A condição está no `UPDATE`, não no lock | `test_concorrencia_sem_lock_eficaz_ainda_confirma_uma_unica_reserva` |
| **Queda do primário PostgreSQL** | Promoção manual da réplica; RPO > 0 | Fora do caminho do lock; ver [`replicacao-postgres.md`](replicacao-postgres.md) | `scripts/validar_replicacao.sh` |

---

## 7. Limitações conhecidas (honestidade técnica)

| Limitação | Consequência | Mitigação atual / evolução |
|---|---|---|
| **Redis single-node**, não Redlock | Indisponibilidade do Redis ⇒ indisponibilidade de reservas e liberações. Um split-brain de Redis não é tratado. | Fail closed e trade-off consciente de consistência sobre disponibilidade. Redlock/Sentinel exigiriam múltiplos nós — fora do escopo do laboratório. |
| **Sem fencing token** | A checagem de posse e o `COMMIT` não são uma operação única. Um stall do processo exatamente nesse intervalo permitiria duas transações na seção crítica por um instante. | O `UPDATE` condicional e o `CHECK` impedem venda dupla mesmo nesse caso: o lock não é a última linha de defesa. Fencing token versionado no banco é a evolução natural. |
| **TTL fixo de 5 s** | Cada operação em curso custa um `EVAL` de renovação a cada ≈ 1,67 s. Operações muito longas sustentam essa carga por mais tempo. | `LOCK_TTL_SECONDS` é configurável por ambiente; o watchdog tem piso de 50 ms. |
| **Granularidade por item** | Pedidos com vários itens não são atômicos entre si; cada item tem sua própria transação e a compensação é do `servico-pedidos`. | Saga coreografada com `ReleaseReserva` por item, com falhas isoladas e logadas. |
| **Sem fila de espera** | Lock ocupado devolve `ESTOQUE_BLOQUEADO` imediatamente; o cliente decide quando repetir. | Retry com backoff e circuit breaker estão previstos para a Sprint 4; hoje o comportamento é explícito no contrato. |
| **Redis sem volume no Compose** | Um restart do Redis limpa todas as chaves de lock. | É proposital: o lock é estado transiente e não é fonte de verdade. A correção está no PostgreSQL. |
| **Eventos best-effort** | Um `EstoqueAtualizado` pode ser perdido ou duplicado; não há outbox transacional. | Eventos são não-críticos por decisão (ADR-005); `event_id` permite deduplicação futura. |
| **Réplica assíncrona** | Leitura na réplica pode estar defasada; RPO > 0. | Os serviços não leem da réplica; ver ADR-007. |
| **Prova de concorrência limitada na suíte padrão** | A suíte padrão roda com SQLite e `fakeredis`; a concorrência real é demonstrada apenas na suíte marcada `postgres`. | Os testes `-m postgres` existem exatamente para isso e são pulados sem `TEST_DATABASE_URL`; ver seção 8. |

Nenhuma dessas limitações afeta o invariante de negócio declarado (INV-1 a INV-4). Elas
afetam **disponibilidade**, **latência sob contenção** e a **força da exclusão mútua no
intervalo mínimo entre a checagem de posse e o `COMMIT`** — e estão listadas para que a
avaliação do trabalho não dependa de uma leitura otimista do algoritmo.

---

## 8. Como validar

### 8.1 Suíte automatizada

```bash
cd servico-estoque
./.venv/Scripts/python.exe -m pytest -q
# 82 passed, 5 skipped in 13.95s
```

Os 5 testes pulados são os marcados `postgres`, que exigem um PostgreSQL real e são
executados assim:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432/estoque_db \
  ./.venv/Scripts/python.exe -m pytest -m postgres -q
```

> Atenção: a suíte `postgres` **recria as tabelas** do database apontado por
> `TEST_DATABASE_URL`; rode-a apenas contra um database de teste e reaplique o seed depois
> (`scripts/seed_dev.sql`).

Só os testes de exclusão mútua e do lock:

```bash
./.venv/Scripts/python.exe -m pytest tests/test_exclusao_mutua.py tests/test_redis_lock.py -q
# 28 passed in 6.45s
```

### 8.2 O que cada grupo de testes prova

| Grupo | O que demonstra |
|---|---|
| `tests/test_redis_lock.py` | Atomicidade do `SET NX EX`; unicidade da dona entre várias instâncias; scripts Lua de liberação e renovação condicionados ao `request_uuid`; padrão da chave; validação do TTL; watchdog que renova além do TTL e que não mascara a exceção da seção crítica |
| `tests/test_exclusao_mutua.py` | Renovação mantém a exclusão em reserva lenta; `COMMIT` descartado quando o lease é perdido; liberação descartada quando o lease é perdido; Redis caindo entre o lock e o `COMMIT`; ausência de liberação redundante; liberação única no caminho feliz |
| `tests/test_estoque_service.py` | Último item não reservado duas vezes; idempotência por `(pedido_id, item_id)`; `ESTOQUE_BLOQUEADO` com lock ocupado; fail closed com Redis fora; compensação de pedidos multi-item |
| `tests/test_integracao_postgres.py` (`-m postgres`) | Concorrência real no PostgreSQL: 20 reservas simultâneas confirmam uma única reserva — **com** e **sem** lock eficaz —, retries concorrentes debitam uma única vez e o Redis fora falha fechado sem tocar no banco |

### 8.3 Inspeção manual do lock

Com a stack no ar (`docker compose up -d postgres redis servico-estoque`), é possível
observar a semântica de `NX` diretamente:

```bash
# primeira aquisição: OK
docker compose exec redis redis-cli SET lock:estoque:00000000-0000-0000-0000-000000000000 dono-a NX EX 5

# segunda aquisição, com a chave ocupada: nil
docker compose exec redis redis-cli SET lock:estoque:00000000-0000-0000-0000-000000000000 dono-b NX EX 5

# TTL restante e dono atual
docker compose exec redis redis-cli TTL lock:estoque:00000000-0000-0000-0000-000000000000
docker compose exec redis redis-cli GET lock:estoque:00000000-0000-0000-0000-000000000000
```

---

## 9. Referências

- [`docs/arquitetura.md`](arquitetura.md) §3.3 — fluxo de reserva e modelo de dados do `servico-estoque`.
- [`docs/decisoes-tecnicas.md`](decisoes-tecnicas.md) — ADR-004 (Redis Lock) e ADR-007 (replicação assíncrona).
- [`docs/replicacao-postgres.md`](replicacao-postgres.md) §8 — garantias e limitações da réplica.
- [`docs/comunicacoes-rest-grpc.md`](comunicacoes-rest-grpc.md) §3.4 e §3.5 — garantias do serviço e compensação distribuída.
- `servico-estoque/app/locking/redis_lock.py` — implementação do lock, scripts Lua e watchdog.
- `servico-estoque/app/services/estoque_service.py` — orquestração de idempotência, lock, transação e liberação.
- `servico-estoque/app/repositories/estoque_repository.py` — `UPDATE` condicional com `RETURNING`.
