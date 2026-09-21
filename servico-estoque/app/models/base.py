"""Base declarativa do SQLAlchemy para os modelos do servico-estoque."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Classe base de todos os modelos ORM do serviço."""
