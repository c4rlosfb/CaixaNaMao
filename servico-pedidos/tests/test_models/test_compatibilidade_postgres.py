"""Guardas de compatibilidade com PostgreSQL que o SQLite dos testes não pega.

O SQLite aceita `datetime` com timezone em coluna naive; o PostgreSQL (asyncpg)
**recusa** com `can't subtract offset-naive and offset-aware datetimes`. Sem estas
asserções, o serviço passava em toda a suíte e falhava em TODO INSERT no banco
real — bug encontrado na verificação ponta a ponta com PostgreSQL de verdade.
"""

from __future__ import annotations

from app.models.pedido import ItemPedido, Pedido


def test_timestamps_do_pedido_sao_timezone_aware() -> None:
    for nome in ("criado_em", "atualizado_em"):
        coluna = Pedido.__table__.c[nome]
        assert coluna.type.timezone is True, (
            f"{nome} precisa ser DateTime(timezone=True) para casar com o "
            "TIMESTAMP WITH TIME ZONE da migration"
        )


def test_valores_monetarios_usam_numeric_com_precisao_fixa() -> None:
    for tabela, coluna in ((Pedido, "total"), (ItemPedido, "preco_unitario")):
        tipo = tabela.__table__.c[coluna].type
        assert tipo.precision == 12, f"{tabela.__name__}.{coluna} precisa ser NUMERIC(12,2)"
        assert tipo.scale == 2, f"{tabela.__name__}.{coluna} precisa ter 2 casas decimais"


def test_colunas_nao_nulas_das_tabelas_criticas() -> None:
    for nome in ("id", "vendedor_id", "status", "total", "criado_em", "atualizado_em"):
        assert Pedido.__table__.c[nome].nullable is False, f"pedidos.{nome} precisa ser NOT NULL"
