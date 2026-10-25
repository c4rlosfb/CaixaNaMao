"""Distributed lock com Redis para a reserva de estoque (ADR-004).

Aquisição atômica com `SET ... NX EX`, liberação com verificação de
propriedade via Lua e **renovação de lease** enquanto a seção crítica roda —
o algoritmo documentado em `docs/arquitetura.md` §3.3 e no ADR-004.

Por que renovar: o TTL curto protege contra crash (o lock nunca fica preso),
mas uma operação mais lenta que o TTL deixaria o lease expirar no meio da
transação e outra instância entraria na seção crítica ao mesmo tempo. O
watchdog de `manter_vivo()` renova o TTL a cada 1/3 do TTL — sempre condicionado
ao `request_uuid` do dono — e o `EstoqueService` reconfirma a posse
imediatamente antes do `COMMIT`.

Importante (ver mitigação do ADR-004): se o Redis estiver indisponível, o
chamador **falha fechado** — nunca degrada para `SELECT FOR UPDATE` isolado,
pois uma instância sem Redis poderia reservar o mesmo item que outra instância
com o Redis ativo.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Liberação atômica: só o dono do lock (mesmo request_uuid) pode removê-lo.
# Evita que uma requisição cujo lock já expirou apague o lock de outra.
LIBERAR_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

# Renovação atômica: estende o TTL apenas se o dono ainda for o mesmo.
# O TTL vai em milissegundos (PEXPIRE) para não depender de arredondamento.
RENOVAR_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# Piso do intervalo do watchdog: TTLs muito baixos não devem virar busy loop.
INTERVALO_MINIMO_RENOVACAO = 0.05


class LockIndisponivelError(RuntimeError):
    """O Redis não respondeu — o chamador deve falhar fechado."""


class RedisLockManager:
    """Gerencia o ciclo de vida do lock `lock:estoque:{item_id}`."""

    PREFIXO_PADRAO = "lock:estoque"

    def __init__(self, redis_client: object, ttl_seconds: int = 5, prefixo: str = PREFIXO_PADRAO) -> None:
        self._redis = redis_client
        self._ttl = max(int(ttl_seconds), 1)
        self._prefixo = prefixo

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @property
    def intervalo_renovacao(self) -> float:
        """Intervalo do watchdog: 1/3 do TTL.

        Duas renovações seguidas podem falhar sem que o lease expire, o que
        absorve jitter do event loop e latência pontual do Redis.
        """
        return max(self._ttl / 3, INTERVALO_MINIMO_RENOVACAO)

    def chave(self, item_id: uuid.UUID | str) -> str:
        return f"{self._prefixo}:{item_id}"

    async def adquirir(self, item_id: uuid.UUID | str, request_uuid: uuid.UUID | str) -> bool:
        """`SET lock:estoque:{item_id} {request_uuid} NX EX {ttl}`.

        Devolve `True` se o lock foi adquirido, `False` se outra requisição é a
        dona no momento. Levanta `LockIndisponivelError` se o Redis falhar.
        """
        chave = self.chave(item_id)
        try:
            resultado = await self._redis.set(chave, str(request_uuid), nx=True, ex=self._ttl)
        except RedisError as exc:
            raise LockIndisponivelError(f"Redis indisponível ao adquirir {chave}: {exc}") from exc
        except OSError as exc:  # conexão recusada / DNS
            raise LockIndisponivelError(f"Redis inacessível ao adquirir {chave}: {exc}") from exc
        return bool(resultado)

    async def liberar(self, item_id: uuid.UUID | str, request_uuid: uuid.UUID | str) -> bool:
        """Executa o script Lua de liberação com checagem de propriedade.

        Devolve `True` se o lock era nosso e foi removido; `False` se já não era
        mais nosso (TTL expirado ou outra requisição é dona). Levanta
        `LockIndisponivelError` se o Redis falhar.
        """
        chave = self.chave(item_id)
        try:
            resultado = await self._redis.eval(LIBERAR_LOCK_LUA, 1, chave, str(request_uuid))
        except RedisError as exc:
            raise LockIndisponivelError(f"Redis indisponível ao liberar {chave}: {exc}") from exc
        except OSError as exc:
            raise LockIndisponivelError(f"Redis inacessível ao liberar {chave}: {exc}") from exc
        return bool(resultado)

    async def renovar(self, item_id: uuid.UUID | str, request_uuid: uuid.UUID | str) -> bool:
        """Estende o TTL do lock se — e somente se — ainda formos o dono.

        Devolve `False` quando o lease já expirou ou foi tomado por outra
        instância: nesse caso a seção crítica **não** está mais protegida e o
        chamador deve descartar a transação. Levanta `LockIndisponivelError` se
        o Redis falhar (fail closed).
        """
        chave = self.chave(item_id)
        try:
            resultado = await self._redis.eval(
                RENOVAR_LOCK_LUA, 1, chave, str(request_uuid), self._ttl * 1000
            )
        except RedisError as exc:
            raise LockIndisponivelError(f"Redis indisponível ao renovar {chave}: {exc}") from exc
        except OSError as exc:
            raise LockIndisponivelError(f"Redis inacessível ao renovar {chave}: {exc}") from exc
        return bool(resultado)

    async def confirmar_posse(self, item_id: uuid.UUID | str, request_uuid: uuid.UUID | str) -> bool:
        """Consulta, sem alterar o TTL, se o lock ainda pertence a `request_uuid`.

        Usado antes do `COMMIT`: se o lease foi perdido, a transação é
        descartada em vez de disputar a seção crítica com a nova dona.
        """
        chave = self.chave(item_id)
        try:
            valor = await self._redis.get(chave)
        except RedisError as exc:
            raise LockIndisponivelError(f"Redis indisponível ao consultar {chave}: {exc}") from exc
        except OSError as exc:
            raise LockIndisponivelError(f"Redis inacessível ao consultar {chave}: {exc}") from exc
        if isinstance(valor, bytes):
            valor = valor.decode("utf-8", errors="replace")
        return valor == str(request_uuid)

    @asynccontextmanager
    async def manter_vivo(self, item_id: uuid.UUID | str, request_uuid: uuid.UUID | str) -> AsyncIterator[None]:
        """Mantém o lease renovado enquanto o bloco crítico está em execução.

        Sobe uma tarefa que chama `renovar()` a cada `intervalo_renovacao` e a
        cancela na saída (inclusive em exceção). Perder a posse ou o Redis
        durante a renovação encerra o watchdog com log de erro; a checagem de
        `confirmar_posse()` antes do `COMMIT` é quem impede o commit inseguro.
        """
        tarefa = asyncio.create_task(self._renovar_em_loop(item_id, request_uuid))
        try:
            yield
        finally:
            tarefa.cancel()
            with suppress(asyncio.CancelledError):
                await tarefa

    async def _renovar_em_loop(self, item_id: uuid.UUID | str, request_uuid: uuid.UUID | str) -> None:
        chave = self.chave(item_id)
        while True:
            await asyncio.sleep(self.intervalo_renovacao)
            try:
                renovado = await self.renovar(item_id, request_uuid)
            except LockIndisponivelError as exc:
                logger.error(
                    "Renovação do lock %s falhou (%s); seção crítica segue sem garantia de "
                    "exclusão mútua até a checagem de posse no COMMIT",
                    chave,
                    exc,
                )
                return
            if not renovado:
                logger.error(
                    "Lease do lock %s foi perdido (TTL expirou ou outra requisição é dona); "
                    "a seção crítica não está mais sob exclusão mútua",
                    chave,
                )
                return
