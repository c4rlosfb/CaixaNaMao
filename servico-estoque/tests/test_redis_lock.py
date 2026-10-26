"""Testes do lock distribuído (ADR-004): SET NX EX + liberação com ownership."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.locking.redis_lock import (
    LIBERAR_LOCK_LUA,
    RENOVAR_LOCK_LUA,
    LockIndisponivelError,
    RedisLockManager,
)


class RedisQuebrado:
    """Redis que não responde — prova o fail closed de renovação/posse."""

    async def get(self, *args, **kwargs):
        raise ConnectionError("Connection refused")

    async def eval(self, *args, **kwargs):
        raise ConnectionError("Connection refused")


async def test_adquire_lock_com_nx_ex(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    dono = uuid.uuid4()

    assert await lock_manager.adquirir(item_id, dono) is True
    # Segunda tentativa (mesmo dono ou outro) falha enquanto o lock existe — NX.
    assert await lock_manager.adquirir(item_id, uuid.uuid4()) is False

    ttl = await redis_fake.ttl(lock_manager.chave(item_id))
    assert 0 < ttl <= 5


async def test_liberacao_so_remove_lock_do_proprio_dono(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    intruso = uuid.uuid4()
    chave = lock_manager.chave(item_id)

    await lock_manager.adquirir(item_id, dono)

    # O intruso não consegue liberar o lock alheio (script Lua compara o valor).
    assert await lock_manager.liberar(item_id, intruso) is False
    assert await redis_fake.get(chave) == str(dono)

    # O dono libera.
    assert await lock_manager.liberar(item_id, dono) is True
    assert await redis_fake.get(chave) is None


async def test_script_lua_retorna_zero_quando_a_chave_nao_existe(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    assert await redis_fake.eval(LIBERAR_LOCK_LUA, 1, lock_manager.chave(item_id), "x") == 0


async def test_chave_do_lock_segue_o_padrao_documentado(lock_manager):
    item_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert lock_manager.chave(item_id) == f"lock:estoque:{item_id}"


def test_ttl_configuravel():
    assert RedisLockManager(object(), ttl_seconds=9).ttl_seconds == 9


@pytest.mark.parametrize("ttl_invalido", [0, -5, False, True, 2.5, "5", None])
def test_ttl_invalido_e_recusado_com_valueerror(ttl_invalido):
    """TTL inválido não pode ser convertido silenciosamente para 1 s.

    `bool` é subclasse de `int` e também precisa ser recusado; `ttl_seconds`
    só aceita inteiro maior que zero.
    """
    with pytest.raises(ValueError, match="ttl_seconds"):
        RedisLockManager(object(), ttl_seconds=ttl_invalido)


# ---------------------------------------------------------------------------
# O watchdog é acessório: falhas dele não podem derrubar a seção crítica
# ---------------------------------------------------------------------------
class RedisComFalhaInesperadaNaRenovacao:
    """`SET`/`GET` funcionam; o `EVAL` de renovação levanta erro não-Redis."""

    def __init__(self, real) -> None:
        self._real = real

    async def set(self, *args, **kwargs):
        return await self._real.set(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return await self._real.get(*args, **kwargs)

    async def eval(self, *args, **kwargs):
        raise RuntimeError("pool do Redis corrompido")


class LockComWatchdogQuebrado(RedisLockManager):
    """Watchdog que morre com exceção fora de `LockIndisponivelError`."""

    async def _renovar_em_loop(self, item_id, request_uuid) -> None:
        raise RuntimeError("watchdog quebrou")


async def test_manter_vivo_nao_propaga_excecao_inesperada_do_watchdog(redis_fake):
    """Erro inesperado na renovação não aborta o fluxo principal.

    A decisão sobre commit inseguro continua sendo `confirmar_posse()` antes do
    COMMIT; o watchdog não pode substituir a exceção da seção crítica por uma
    própria (era o `await tarefa` do `finally` relançando).
    """
    lock = RedisLockManager(RedisComFalhaInesperadaNaRenovacao(redis_fake), ttl_seconds=1)
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    assert await lock.adquirir(item_id, dono) is True

    async with lock.manter_vivo(item_id, dono):
        await asyncio.sleep(0.6)  # > intervalo de renovação (1/3 s)
        # seção crítica segue normalmente

    assert await redis_fake.get(lock.chave(item_id)) == str(dono)


async def test_excecao_da_secao_critica_prevalece_sobre_falha_do_watchdog(redis_fake):
    """A exceção original da transação chega ao chamador, não a do watchdog."""
    lock = LockComWatchdogQuebrado(redis_fake, ttl_seconds=5)
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    assert await lock.adquirir(item_id, dono) is True

    with pytest.raises(ValueError, match="falha da transacao"):
        async with lock.manter_vivo(item_id, dono):
            await asyncio.sleep(0)  # deixa o watchdog falhar antes
            raise ValueError("falha da transacao")


# ---------------------------------------------------------------------------
# Renovação de lease: sem ela, uma seção crítica mais longa que o TTL perde a
# exclusão mútua no meio da operação (Issue 11 / ADR-004).
# ---------------------------------------------------------------------------
async def test_apenas_uma_entre_varias_instancias_adquire_o_mesmo_item(redis_fake):
    lock = RedisLockManager(redis_fake, ttl_seconds=5)
    item_id = uuid.uuid4()

    resultados = await asyncio.gather(
        *[lock.adquirir(item_id, uuid.uuid4()) for _ in range(20)]
    )

    assert resultados.count(True) == 1


async def test_renovar_estende_o_ttl_do_dono(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    chave = lock_manager.chave(item_id)
    assert await lock_manager.adquirir(item_id, dono) is True

    await redis_fake.expire(chave, 1)  # lease quase vencendo

    assert await lock_manager.renovar(item_id, dono) is True
    assert await redis_fake.ttl(chave) > 1


async def test_renovar_nao_estende_lock_de_outro_dono(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    intruso = uuid.uuid4()
    chave = lock_manager.chave(item_id)
    assert await lock_manager.adquirir(item_id, dono) is True
    await redis_fake.expire(chave, 2)

    assert await lock_manager.renovar(item_id, intruso) is False
    assert await redis_fake.ttl(chave) <= 2
    assert await redis_fake.get(chave) == str(dono)


async def test_renovar_sem_lock_retorna_false(redis_fake, lock_manager):
    assert await lock_manager.renovar(uuid.uuid4(), uuid.uuid4()) is False


async def test_script_de_renovacao_retorna_zero_quando_a_chave_nao_existe(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    assert (
        await redis_fake.eval(RENOVAR_LOCK_LUA, 1, lock_manager.chave(item_id), "x", 5000) == 0
    )


async def test_confirmar_posse_so_reconhece_o_dono(redis_fake, lock_manager):
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    chave = lock_manager.chave(item_id)
    assert await lock_manager.adquirir(item_id, dono) is True

    assert await lock_manager.confirmar_posse(item_id, dono) is True
    assert await lock_manager.confirmar_posse(item_id, uuid.uuid4()) is False

    await redis_fake.delete(chave)
    assert await lock_manager.confirmar_posse(item_id, dono) is False


async def test_confirmar_posse_com_redis_fora_levanta_lockindisponivel():
    lock = RedisLockManager(RedisQuebrado(), ttl_seconds=5)
    with pytest.raises(LockIndisponivelError):
        await lock.confirmar_posse(uuid.uuid4(), uuid.uuid4())


async def test_manter_vivo_renova_o_lease_alem_do_ttl(redis_fake):
    """Watchdog: uma seção crítica de 2x o TTL continua dona do lock.

    Sem a renovação, a chave expiraria em 1 s e o assert dentro do bloco
    falharia — é exatamente a corrida que a Issue 11 pede para eliminar.
    """
    lock = RedisLockManager(redis_fake, ttl_seconds=1)
    item_id = uuid.uuid4()
    dono = uuid.uuid4()
    chave = lock.chave(item_id)
    assert await lock.adquirir(item_id, dono) is True

    async with lock.manter_vivo(item_id, dono):
        await asyncio.sleep(2.2)  # 2,2x o TTL
        assert await redis_fake.get(chave) == str(dono)

    # Fora do bloco o watchdog é cancelado: o TTL de 1 s volta a valer e a
    # chave expira sozinha (nenhuma renovação órfã fica rodando).
    await asyncio.sleep(1.3)
    assert await redis_fake.get(chave) is None

