"""Enumerações de domínio do servico-estoque."""

from __future__ import annotations

from enum import Enum


class TipoMovimentacao(str, Enum):
    """Tipos de movimentação registrados no ledger (`movimentacoes.tipo`).

    O ledger é a fonte de verdade da idempotência: a restrição única
    `(pedido_id, tipo)` garante que um mesmo pedido não reserve nem libere
    estoque duas vezes (retry do cliente gera o mesmo `pedido_id`).
    """

    RESERVA = "RESERVA"
    LIBERACAO = "LIBERACAO"
