"""Testes do lock distribuído (ADR-004): SET NX EX + liberação com ownership."""

from __future__ import annotations

import uuid

from app.locking.redis_lock import LIBERAR_LOCK_LUA, RedisLockManager


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
