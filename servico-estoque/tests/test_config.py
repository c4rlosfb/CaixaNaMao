"""Testes dos utilitários de configuração."""

from __future__ import annotations

from app.config import alvo_banco_sanitizado


def test_alvo_banco_sanitizado_remove_credenciais_e_querystring():
    url = "postgresql+asyncpg://estoque_user:senha-secreta@postgres:5432/estoque_db?sslmode=require"

    alvo = alvo_banco_sanitizado(url)

    assert alvo == "postgres:5432/estoque_db"
    assert "senha-secreta" not in alvo
    assert "sslmode" not in alvo


def test_alvo_banco_sanitizado_sem_credenciais_mantem_o_resto():
    url = "sqlite+aiosqlite:///./estoque_test.db"

    assert alvo_banco_sanitizado(url) == url


def test_alvo_banco_sanitizado_com_querystring_sem_credenciais():
    url = "postgresql+asyncpg://postgres:5432/estoque_db?options=-c%20statement_timeout%3D5000"

    alvo = alvo_banco_sanitizado(url)

    assert alvo == "postgresql+asyncpg://postgres:5432/estoque_db"
    assert "options" not in alvo
