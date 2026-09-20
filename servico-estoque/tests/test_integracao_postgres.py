"""Testes de integração contra **PostgreSQL real** — a garantia central do serviço.

Marcados com `postgres` e pulados quando `TEST_DATABASE_URL` não está definida:

    TEST_DATABASE_URL=postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432/estoque_db \
        python -m pytest -m postgres -q

Existem porque SQLite não reproduz a concorrência real de transações: é o
PostgreSQL que garante, pela condição no próprio `UPDATE`, que o último item não
seja vendido duas vezes (ADR-004 + rec. 11 do review do PR #23). Sem estes testes,
a garantia ficaria apenas descrita no texto do PR.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.locking.redis_lock import RedisLockManager
from app.models.base import Base
from app.models.item import Item
from app.models.movimentacao import Movimentacao
from app.services.estoque_service import EstoqueService, StatusReserva

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="defina TEST_DATABASE_URL para rodar os testes de integração (PostgreSQL real)",
    ),
]

N_CONCORRENTES = 20


class LockNulo:
    """Lock que sempre 'adquire': isola a garantia do banco da garantia do Redis."""

    ttl_seconds = 5

    def chave(self, item_id) -> str:
        return f"lock:estoque:{item_id}"

    async def adquirir(self, *args, **kwargs) -> bool:
        return True

    async def liberar(self, *args, **kwargs) -> bool:
        return True


class RedisFora:
    async def set(self, *args, **kwargs):
        raise ConnectionError("Connection refused")

    async def eval(self, *args, **kwargs):
        raise ConnectionError("Connection refused")


def lock_real() -> RedisLockManager:
    return RedisLockManager(fakeredis.aioredis.FakeRedis(decode_responses=True), ttl_seconds=5)


@pytest_asyncio.fixture
async def engine():
    """Engine contra o PostgreSQL de teste.

    As tabelas são **recriadas** a cada execução a partir dos modelos: assim o
    teste nunca roda contra um schema obsoleto (foi o que aconteceu quando a
    migration 0001 mudou e o banco de teste continuava com a constraint antiga).
    Aponte `TEST_DATABASE_URL` para um database descartável — em dev, o
    `scripts/seed_dev.sql` recria os itens de exemplo.
    """
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessao_factory(engine) -> async_sessionmaker:
    """Mesma fábrica de sessões que o servidor usa (uma sessão por operação)."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def _criar_item(sessao_factory, quantidade: int, nome: str = "Item de teste") -> uuid.UUID:
    async with sessao_factory() as session:
        item = Item(
            nome=nome, quantidade_disponivel=quantidade, quantidade_reservada=0
        )
        session.add(item)
        await session.commit()
        return item.id


async def _reservar(sessao_factory, pedido_id, item_id, quantidade, lock):
    async with sessao_factory() as session:
        servico = EstoqueService(session, lock)
        return await servico.reservar(
            item_id=item_id,
            quantidade=quantidade,
            pedido_id=pedido_id,
            request_uuid=uuid.uuid4(),
        )


async def _estado(sessao_factory, item_id) -> tuple[int, int, int]:
    async with sessao_factory() as session:
        item = await session.get(Item, item_id)
        movimentacoes = await session.scalar(
            select(func.count()).select_from(Movimentacao).where(Movimentacao.item_id == item_id)
        )
        return item.quantidade_disponivel, item.quantidade_reservada, movimentacoes


async def _reservas_concorrentes(sessao_factory, item_id, lock, quantidade: int = 1):
    return await asyncio.gather(
        *[
            _reservar(sessao_factory, uuid.uuid4(), item_id, quantidade, lock)
            for _ in range(N_CONCORRENTES)
        ]
    )


# ---------------------------------------------------------------------------
# A garantia central: 20 reservas simultâneas para um item de estoque 1
# ---------------------------------------------------------------------------
async def test_concorrencia_com_lock_do_redis_confirma_uma_unica_reserva(sessao_factory):
    item_id = await _criar_item(sessao_factory, quantidade=1)

    resultados = await _reservas_concorrentes(sessao_factory, item_id, lock_real())
    status = [r.status.value for r in resultados]

    # A invariante é: exatamente uma reserva confirmada e o saldo íntegro. Quem
    # perde recebe ESTOQUE_BLOQUEADO (lock ocupado) ou ESTOQUE_INSUFICIENTE (já
    # sem saldo quando conseguiu o lock) — ambos são desfechos válidos.
    assert status.count("CONFIRMADO") == 1
    assert set(status) <= {"CONFIRMADO", "ESTOQUE_BLOQUEADO", "ESTOQUE_INSUFICIENTE"}
    assert await _estado(sessao_factory, item_id) == (0, 1, 1)


async def test_concorrencia_sem_lock_eficaz_ainda_confirma_uma_unica_reserva(sessao_factory):
    """O UPDATE condicional no PostgreSQL é quem impede a venda dupla.

    Com o lock neutralizado (TTL expirado, instância sem coordenação), a garantia
    precisa continuar valendo — é exatamente o cenário que a sabotagem do PR
    demonstrou: trocando o UPDATE por read-then-write, 15 das 20 requisições
    foram confirmadas para o mesmo item de 1 unidade.
    """
    item_id = await _criar_item(sessao_factory, quantidade=1)

    resultados = await _reservas_concorrentes(sessao_factory, item_id, LockNulo())
    status = [r.status.value for r in resultados]

    assert status.count("CONFIRMADO") == 1
    assert status.count("ESTOQUE_INSUFICIENTE") == N_CONCORRENTES - 1
    assert await _estado(sessao_factory, item_id) == (0, 1, 1)


async def test_retries_concorrentes_do_mesmo_item_debitam_uma_unica_vez(sessao_factory):
    item_id = await _criar_item(sessao_factory, quantidade=10)
    pedido_id = uuid.uuid4()

    resultados = await asyncio.gather(
        *[
            _reservar(sessao_factory, pedido_id, item_id, 3, lock_real())
            for _ in range(10)
        ]
    )
    status = [r.status.value for r in resultados]

    # Um único débito de 3 (disponível 7, reservada 3) e uma única movimentação;
    # retries concorrentes podem receber ESTOQUE_BLOQUEADO (lock ocupado) e devem
    # ser refeitos, mas nunca debitam de novo.
    assert status.count("ESTOQUE_INSUFICIENTE") == 0
    assert await _estado(sessao_factory, item_id) == (7, 3, 1)


async def test_redis_indisponivel_falha_fechado_sem_tocar_no_banco(sessao_factory):
    item_id = await _criar_item(sessao_factory, quantidade=10)

    resultado = await _reservar(
        sessao_factory, uuid.uuid4(), item_id, 1, RedisLockManager(RedisFora())
    )

    assert resultado.status is StatusReserva.ESTOQUE_BLOQUEADO
    assert await _estado(sessao_factory, item_id) == (10, 0, 0)


async def test_pedido_com_dois_itens_reserva_os_dois_no_postgres(sessao_factory):
    """Regressão do bloqueador do review: a chave do ledger é (pedido, item, tipo)."""
    item_a = await _criar_item(sessao_factory, quantidade=4, nome="Coxinha")
    item_b = await _criar_item(sessao_factory, quantidade=4, nome="Pastel")
    pedido_id = uuid.uuid4()
    lock = lock_real()

    reserva_a = await _reservar(sessao_factory, pedido_id, item_a, 1, lock)
    reserva_b = await _reservar(sessao_factory, pedido_id, item_b, 2, lock)

    assert reserva_a.status is StatusReserva.CONFIRMADO
    assert reserva_b.status is StatusReserva.CONFIRMADO
    assert await _estado(sessao_factory, item_a) == (3, 1, 1)
    assert await _estado(sessao_factory, item_b) == (2, 2, 1)
