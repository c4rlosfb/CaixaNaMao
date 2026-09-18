"""Dependências FastAPI injetáveis: sessão de banco e usuário autenticado."""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.jwt import TokenPayload, decode_token
from app.config import settings

# ---------------------------------------------------------------------------
# Engine e SessionFactory (singleton por processo)
# ---------------------------------------------------------------------------

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionFactory = async_sessionmaker(engine, expire_on_commit=False)


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
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> TokenPayload:
    """Valida o Bearer JWT localmente (HS256) e retorna o payload do token.

    Raises:
        HTTPException 401: token ausente, expirado ou inválido.
    """
    return decode_token(credentials.credentials)
