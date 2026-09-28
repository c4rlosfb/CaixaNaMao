"""Módulo de observabilidade e correlação distribuída (UUID tracking).

Referência: docs/arquitetura.md e ADR-006.

Padroniza o rastreio de transações ponta a ponta:
- X-Correlation-ID: identifica a transação de negócio ao cruzar limites de serviço;
- X-Request-ID: identifica a requisição individual a este serviço;
- Propagação via contexto assíncrono (contextvars);
- Propagação automática em chamadas gRPC (metadados x-correlation-id e x-request-id);
- Propagação em eventos SQS (envelope JSON e MessageAttributes);
- Filtro de log para formato unificado com [corr=<uuid> req=<uuid>].
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_ID_HEADER = "x-correlation-id"
REQUEST_ID_HEADER = "x-request-id"
CORRELATION_ID_HTTP_HEADER = "X-Correlation-ID"
REQUEST_ID_HTTP_HEADER = "X-Request-ID"

LOG_FORMAT = "%(levelname)s | %(name)s | [corr=%(correlation_id)s req=%(request_id)s] | %(message)s"

_correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def validar_uuid(valor: str | None) -> str | None:
    """Valida se o valor recebido é um UUID válido; retorna a representação canônica em minúsculas ou None."""
    if not valor:
        return None
    try:
        return str(uuid.UUID(str(valor).strip()))
    except (ValueError, AttributeError, TypeError):
        return None


def validar_ou_gerar_uuid(valor: str | None = None) -> str:
    """Valida o UUID fornecido ou gera um novo UUIDv4 se for nulo ou inválido."""
    valido = validar_uuid(valor)
    return valido if valido is not None else str(uuid.uuid4())


def get_correlation_id() -> str | None:
    """Retorna o correlation_id do contexto assíncrono atual."""
    return _correlation_id_ctx.get()


def get_request_id() -> str | None:
    """Retorna o request_id do contexto assíncrono atual."""
    return _request_id_ctx.get()


def set_correlation_id(valor: str | None) -> None:
    """Define o correlation_id no contexto assíncrono atual."""
    _correlation_id_ctx.set(valor)


def set_request_id(valor: str | None) -> None:
    """Define o request_id no contexto assíncrono atual."""
    _request_id_ctx.set(valor)


@contextmanager
def correlation_scope(
    correlation_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[tuple[str, str]]:
    """Context manager para delimitar o escopo de uma transação assíncrona."""
    corr_id = validar_ou_gerar_uuid(correlation_id)
    req_id = validar_ou_gerar_uuid(request_id)
    tok_corr = _correlation_id_ctx.set(corr_id)
    tok_req = _request_id_ctx.set(req_id)
    try:
        yield corr_id, req_id
    finally:
        _correlation_id_ctx.reset(tok_corr)
        _request_id_ctx.reset(tok_req)


class CorrelationLogFilter(logging.Filter):
    """Injeta `correlation_id` e `request_id` em todos os LogRecords formatados."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id() or "-"
        record.request_id = get_request_id() or "-"
        return True


def configurar_logging(level: str | int = logging.INFO) -> None:
    """Configura o logger raiz com formatação uniforme e filtro de correlação."""
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Verifica se já existe um manipulador com CorrelationLogFilter para evitar duplicatas
    filtro_presente = any(isinstance(f, CorrelationLogFilter) for f in root_logger.filters)
    if not filtro_presente:
        root_logger.addFilter(CorrelationLogFilter())

    # Atualiza formatters existentes ou configura StreamHandler padrão
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(CorrelationLogFilter())
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
            if not any(isinstance(f, CorrelationLogFilter) for f in handler.filters):
                handler.addFilter(CorrelationLogFilter())


class CorrelationMiddleware:
    """Middleware ASGI para captura, validação e propagação de correlação HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_corr = headers.get(b"x-correlation-id") or headers.get(b"x-request-id")
        corr_header = raw_corr.decode("latin1") if raw_corr else None
        raw_req = headers.get(b"x-request-id")
        req_header = raw_req.decode("latin1") if raw_req else None

        corr_id = validar_ou_gerar_uuid(corr_header)
        req_id = validar_ou_gerar_uuid(req_header)

        with correlation_scope(correlation_id=corr_id, request_id=req_id):
            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers_list = list(message.get("headers", []))
                    headers_list.append((b"x-correlation-id", corr_id.encode("latin1")))
                    headers_list.append((b"x-request-id", req_id.encode("latin1")))
                    message["headers"] = headers_list
                await send(message)

            await self.app(scope, receive, send_wrapper)


def obter_metadados_grpc() -> list[tuple[str, str]]:
    """Gera metadados gRPC (x-correlation-id e x-request-id) a partir do contexto atual."""
    corr_id = get_correlation_id() or str(uuid.uuid4())
    req_id = get_request_id() or str(uuid.uuid4())
    return [
        (CORRELATION_ID_HEADER, corr_id),
        (REQUEST_ID_HEADER, req_id),
    ]
