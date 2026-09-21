"""Validação local de JWT (HS256) — sem chamada de rede ao servico-autenticacao.

Decisão de arquitetura: ADR-001 + docs/arquitetura.md §6.
O segredo compartilhado é injetado via variável de ambiente JWT_SECRET.
"""

import uuid
from typing import Any

import jwt
from fastapi import HTTPException, status

from app.config import settings


class TokenPayload:
    """Dados extraídos do JWT após validação bem-sucedida."""

    def __init__(self, sub: str, **kwargs: Any) -> None:
        self.vendedor_id = uuid.UUID(sub)
        self.extra = kwargs


def decode_token(token: str) -> TokenPayload:
    """Decodifica e valida um JWT HS256.

    Raises:
        HTTPException 401: se o token for inválido, expirado ou malformado.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        sub = payload.get("sub")
        if not sub:
            raise ValueError("Campo 'sub' ausente no token")
        return TokenPayload(sub=sub, **{k: v for k, v in payload.items() if k != "sub"})
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
