"""Exclusão mútua no fluxo de baixa de estoque (Issue 11 / ADR-004).

Complementa `test_redis_lock.py`: aqui o alvo é o `EstoqueService`. O lease é
renovado por um watchdog enquanto a seção crítica roda e a posse do lock é
reconferida imediatamente antes do `COMMIT`.

Sem esses dois mecanismos, uma reserva mais lenta que o TTL do lock deixaria o
lease expirar no meio da transação: outra instância entraria na seção crítica
ao mesmo tempo e as duas poderiam reservar o mesmo item.
"""

from __future__ import annotations

import asyncio
import uuid

from app.locking.redis_lock import RedisLockManager
from app.models.item import Item
from app.repositories.estoque_repository import EstoqueRepository
from app.services.estoque_service import EstoqueService, StatusLiberacao, StatusReserva


class RedisQueCaiDepoisDoLock:
    """`SET` do lock funciona; a partir daí o Redis "cai" (get/eval falham)."""

    def __init__(self, real) -> None:
        self._real = real
        self._caiu = False

    async def set(self, *args, **kwargs):
        resultado = await self._real.set(*args, **kwargs)
        self._caiu = True
        return resultado

    async def get(self, *args, **kwargs):
        if self._caiu:
            raise ConnectionError("Connection refused")
        return await self._real.get(*args, **kwargs)

    async def eval(self, *args, **kwargs):
        if self._caiu:
            raise ConnectionError("Connection refused")
        return await self._real.eval(*args, **kwargs)


async def _criar_item(session_factory, quantidade: int = 10) -> uuid.UUID:
    async with session_factory() as session:
        item = Item(nome="Coxinha", quantidade_disponivel=quantidade, quantidade_reservada=0)
        session.add(item)
        await session.commit()
        return item.id


async def _estado(session_factory, item_id: uuid.UUID) -> tuple[int, int]:
    async with session_factory() as session:
        item = await session.get(Item, item_id)
        return item.quantidade_disponivel, item.quantidade_reservada


async def _reservar(
    session_factory, item_id: uuid.UUID, quantidade: int, lock, pedido_id: uuid.UUID | None = None
) -> object:
    async with session_factory() as session:
        servico = EstoqueService(session, lock)
        return await servico.reservar(
            item_id=item_id,
            quantidade=quantidade,
            pedido_id=pedido_id or uuid.uuid4(),
            request_uuid=uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# Renovação do lease durante a seção crítica
# ---------------------------------------------------------------------------
async def test_lease_renovado_mantem_a_exclusao_mutua_em_reserva_lenta(
    session_factory, redis_fake, monkeypatch
):
    """Seção crítica de 1,5x o TTL: a concorrente continua barrada pelo lock."""
    item_id = await _criar_item(session_factory, quantidade=1)
    lock = RedisLockManager(redis_fake, ttl_seconds=1)
    reserva_original = EstoqueRepository.reservar_atomico

    async def reserva_lenta(self, *args, **kwargs):
        await asyncio.sleep(1.5)  # 1,5x o TTL — sem renovação o lease expiraria
        return await reserva_original(self, *args, **kwargs)

    monkeypatch.setattr(EstoqueRepository, "reservar_atomico", reserva_lenta)

    primeira = asyncio.create_task(_reservar(session_factory, item_id, 1, lock))
    await asyncio.sleep(1.2)  # já passou do TTL original de 1 s
    segunda = await _reservar(session_factory, item_id, 1, lock)
    resultado_primeira = await primeira

    # A concorrente não entra na seção crítica: o lease foi renovado.
    assert segunda.status is StatusReserva.ESTOQUE_BLOQUEADO
    assert resultado_primeira.status is StatusReserva.CONFIRMADO
    assert await _estado(session_factory, item_id) == (0, 1)


# ---------------------------------------------------------------------------
# Reconfirmação de posse antes do COMMIT
# ---------------------------------------------------------------------------
async def test_commit_e_descartado_quando_o_lease_e_perdido(
    session_factory, redis_fake, monkeypatch
):
    """Lease tomado por outra instância no meio da operação ⇒ rollback."""
    item_id = await _criar_item(session_factory, quantidade=10)
    lock = RedisLockManager(redis_fake, ttl_seconds=5)
    reserva_original = EstoqueRepository.reservar_atomico
    intruso = uuid.uuid4()

    async def reserva_com_lease_tomado(self, *args, **kwargs):
        item = await reserva_original(self, *args, **kwargs)
        # Simula o lease expirando e outra instância adquirindo o lock durante
        # a seção crítica (Redis reiniciado, GC/pausa longa).
        await redis_fake.delete(lock.chave(item_id))
        await redis_fake.set(lock.chave(item_id), str(intruso), ex=5)
        return item

    monkeypatch.setattr(EstoqueRepository, "reservar_atomico", reserva_com_lease_tomado)

    resultado = await _reservar(session_factory, item_id, 2, lock)

    assert resultado.status is StatusReserva.ESTOQUE_BLOQUEADO
    assert "descartada" in resultado.mensagem
    assert await _estado(session_factory, item_id) == (10, 0)


async def test_liberacao_e_descartada_quando_o_lease_e_perdido(
    session_factory, redis_fake, monkeypatch
):
    item_id = await _criar_item(session_factory, quantidade=2)
    lock = RedisLockManager(redis_fake, ttl_seconds=5)
    pedido_id = uuid.uuid4()
    reserva = await _reservar(session_factory, item_id, 2, lock, pedido_id=pedido_id)
    assert reserva.status is StatusReserva.CONFIRMADO

    liberar_original = EstoqueRepository.liberar_atomico

    async def liberacao_com_lease_tomado(self, *args, **kwargs):
        item = await liberar_original(self, *args, **kwargs)
        await redis_fake.delete(lock.chave(item_id))
        await redis_fake.set(lock.chave(item_id), str(uuid.uuid4()), ex=5)
        return item

    monkeypatch.setattr(EstoqueRepository, "liberar_atomico", liberacao_com_lease_tomado)

    async with session_factory() as session:
        servico = EstoqueService(session, lock)
        resultado = await servico.liberar(
            item_id=item_id,
            quantidade=2,
            pedido_id=pedido_id,
            request_uuid=uuid.uuid4(),
        )

    assert resultado.status is StatusLiberacao.ESTOQUE_BLOQUEADO
    assert "descartada" in resultado.mensagem
    assert await _estado(session_factory, item_id) == (0, 2)


async def test_redis_cai_entre_o_lock_e_o_commit_e_a_reserva_e_recusada(
    session_factory, redis_fake
):
    """Fail closed: sem como confirmar a posse, a transação não é commitada."""
    item_id = await _criar_item(session_factory, quantidade=10)
    lock = RedisLockManager(RedisQueCaiDepoisDoLock(redis_fake), ttl_seconds=5)

    resultado = await _reservar(session_factory, item_id, 3, lock)

    assert resultado.status is StatusReserva.ESTOQUE_BLOQUEADO
    assert "descartada" in resultado.mensagem
    assert await _estado(session_factory, item_id) == (10, 0)


# ---------------------------------------------------------------------------
# Liberação do lock no finally
# ---------------------------------------------------------------------------
async def test_lease_perdido_nao_dispara_liberacao_redundante(
    session_factory, redis_fake, monkeypatch
):
    """Se a posse já foi perdida antes do COMMIT, não há EVAL de liberação extra.

    O lock não é mais nosso: liberar de novo só gastaria um round-trip e emitiria
    o warning enganoso de "TTL expirado durante a operação".
    """
    item_id = await _criar_item(session_factory, quantidade=10)
    lock = RedisLockManager(redis_fake, ttl_seconds=5)
    reserva_original = EstoqueRepository.reservar_atomico
    liberar_original = lock.liberar
    liberacoes: list[tuple] = []

    async def reserva_com_lease_tomado(self, *args, **kwargs):
        item = await reserva_original(self, *args, **kwargs)
        await redis_fake.delete(lock.chave(item_id))
        await redis_fake.set(lock.chave(item_id), str(uuid.uuid4()), ex=5)
        return item

    async def liberar_espiao(*args, **kwargs):
        liberacoes.append(args)
        return await liberar_original(*args, **kwargs)

    monkeypatch.setattr(EstoqueRepository, "reservar_atomico", reserva_com_lease_tomado)
    monkeypatch.setattr(lock, "liberar", liberar_espiao)

    resultado = await _reservar(session_factory, item_id, 2, lock)

    assert resultado.status is StatusReserva.ESTOQUE_BLOQUEADO
    assert "descartada" in resultado.mensagem
    assert liberacoes == []
    assert await _estado(session_factory, item_id) == (10, 0)


async def test_reserva_normal_libera_o_lock_uma_unica_vez(redis_fake, session_factory, monkeypatch):
    """Controle: no caminho feliz a liberação continua acontecendo (uma vez)."""
    item_id = await _criar_item(session_factory, quantidade=10)
    lock = RedisLockManager(redis_fake, ttl_seconds=5)
    liberar_original = lock.liberar
    liberacoes: list[tuple] = []

    async def liberar_espiao(*args, **kwargs):
        liberacoes.append(args)
        return await liberar_original(*args, **kwargs)

    monkeypatch.setattr(lock, "liberar", liberar_espiao)

    resultado = await _reservar(session_factory, item_id, 1, lock)

    assert resultado.status is StatusReserva.CONFIRMADO
    assert len(liberacoes) == 1
    assert await redis_fake.get(lock.chave(item_id)) is None
