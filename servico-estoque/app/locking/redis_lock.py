"""Distributed lock com Redis para a reserva de estoque (ADR-004).

Aquisição atômica com `SET ... NX EX` e liberação com verificação de
propriedade via Lua — exatamente o algoritmo documentado em
`docs/arquitetura.md` §3.3 e no ADR-004.

Importante (ver mitigação do ADR-004): se o Redis estiver indisponível, o
chamador **falha fechado** — nunca degrada para `SELECT FOR UPDATE` isolado,
pois uma instância sem Redis poderia reservar o mesmo item que outra instância
com o Redis ativo.
"""

from __future__ import annotations

import logging
import uuid

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


class LockIndisponivelError(RuntimeError):
    """O Redis não respondeu — o chamador deve falhar fechado."""


class RedisLockManager:
    """Gerencia o ciclo de vida do lock `lock:estoque:{item_id}`."""

    PREFIXO_PADRAO = "lock:estoque"

    def __init__(self, redis_client: object, ttl_seconds: int = 5, prefixo: str = PREFIXO_PADRAO) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._prefixo = prefixo

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

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
