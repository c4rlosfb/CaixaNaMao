"""Router de produtos — consulta de catálogo e saldos de estoque via gRPC."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status

from app.clients.estoque_client import EstoqueClient, get_estoque_client
from app.config import settings
from app.schemas.produto import CATALOGO_PRODUTOS, CATALOGO_PRODUTOS_MAP, ProdutoResponse

router = APIRouter(prefix="/produtos", tags=["produtos"])

EstoqueDep = Annotated[EstoqueClient, Depends(get_estoque_client)]


@router.get(
    "/dev-tokens",
    include_in_schema=False,
    summary="Retorna tokens JWT pré-configurados para a demonstração",
)
async def get_dev_tokens() -> dict[str, str]:
    """Gera tokens JWT HS256 válidos com o segredo configurado no serviço (apenas dev/demo)."""
    if not settings.permitir_dev_tokens:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint de tokens de demonstração desabilitado neste ambiente",
        )
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=365)
    carlos_id = "11111111-0000-0000-0000-000000000001"
    luan_id = "22222222-0000-0000-0000-000000000002"
    token_carlos = jwt.encode(
        {"sub": carlos_id, "nome": "Carlos", "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    token_luan = jwt.encode(
        {"sub": luan_id, "nome": "Luan / Eduardo", "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return {
        "vendedor_a": token_carlos,
        "vendedor_b": token_luan,
        "vendedor_a_id": carlos_id,
        "vendedor_b_id": luan_id,
    }


@router.get(
    "",
    response_model=list[ProdutoResponse],
    summary="Lista os produtos do catálogo com saldo atual de estoque",
    description=(
        "Retorna a lista dos 4 produtos base do catálogo (seed_dev.sql), "
        "consultando o saldo de estoque atual de cada um via gRPC no servico-estoque."
    ),
)
async def listar_produtos(
    estoque: EstoqueDep,
) -> list[ProdutoResponse]:
    """Retorna os produtos com os saldos atualizados de estoque em tempo real."""
    async def _montar_produto(base: dict) -> ProdutoResponse:
        resumo = await estoque.consultar_item(base["id"])
        qtd_disponivel = resumo.quantidade_disponivel if resumo is not None else 0
        qtd_reservada = resumo.quantidade_reservada if resumo is not None else 0
        return ProdutoResponse(
            id=base["id"],
            nome=base["nome"],
            descricao=base["descricao"],
            preco=base["preco"],
            quantidade_disponivel=qtd_disponivel,
            quantidade_reservada=qtd_reservada,
            estoque_disponivel=resumo is not None,
        )

    produtos = await asyncio.gather(*[_montar_produto(p) for p in CATALOGO_PRODUTOS])
    return list(produtos)


@router.get(
    "/{produto_id}",
    response_model=ProdutoResponse,
    summary="Consulta os detalhes e saldo de um produto específico",
)
async def get_produto(
    produto_id: uuid.UUID,
    estoque: EstoqueDep,
) -> ProdutoResponse:
    """Busca um produto no catálogo e seu saldo atual no estoque."""
    base = CATALOGO_PRODUTOS_MAP.get(produto_id)
    if not base:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produto {produto_id} não encontrado no catálogo",
        )

    resumo = await estoque.consultar_item(produto_id)
    qtd_disponivel = resumo.quantidade_disponivel if resumo is not None else 0
    qtd_reservada = resumo.quantidade_reservada if resumo is not None else 0

    return ProdutoResponse(
        id=base["id"],
        nome=base["nome"],
        descricao=base["descricao"],
        preco=base["preco"],
        quantidade_disponivel=qtd_disponivel,
        quantidade_reservada=qtd_reservada,
        estoque_disponivel=resumo is not None,
    )
