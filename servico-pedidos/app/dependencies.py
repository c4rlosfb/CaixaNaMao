"""Dependências FastAPI injetáveis: sessão de banco e usuário autenticado."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenPayload, decode_token
from app.db import AsyncSessionFactory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Fornece uma sessão assíncrona do SQLAlchemy com commit/rollback automático."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Autenticação via Bearer JWT
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> TokenPayload:
    """Valida o Bearer JWT localmente (HS256) e retorna o payload do token.

    Raises:
        HTTPException 401: token ausente, expirado ou inválido.
    """
    return decode_token(credentials.credentials)
