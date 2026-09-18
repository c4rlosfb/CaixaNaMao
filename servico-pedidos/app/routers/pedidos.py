"""Router de pedidos — define os 4 endpoints da arquitetura §3.2."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import TokenPayload
from app.clients.estoque_client import EstoqueClient, get_estoque_client
from app.clients.sqs_client import SQSClient, get_sqs_client
from app.dependencies import get_current_user, get_db
from app.repositories.pedido_repository import PedidoRepository
from app.schemas.pedido import PedidoCreate, PedidoListResponse, PedidoResponse
from app.services.pedido_service import PedidoService

router = APIRouter(prefix="/pedidos", tags=["pedidos"])


def _make_service(
    db: AsyncSession = Depends(get_db),
    estoque: EstoqueClient = Depends(get_estoque_client),
    sqs: SQSClient = Depends(get_sqs_client),
) -> PedidoService:
    return PedidoService(db=db, estoque=estoque, sqs=sqs)


@router.post(
    "",
    response_model=PedidoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cria um novo pedido",
    description=(
        "Recebe os itens do pedido, reserva o estoque via gRPC no servico-estoque, "
        "persiste o pedido e publica o evento PedidoCriado no SQS."
    ),
)
async def criar_pedido(
    payload: PedidoCreate,
    current_user: TokenPayload = Depends(get_current_user),
    service: PedidoService = Depends(_make_service),
) -> PedidoResponse:
    pedido = await service.criar_pedido(
        vendedor_id=current_user.vendedor_id,
        payload=payload,
    )
    return PedidoResponse.model_validate(pedido)


@router.get(
    "/{pedido_id}",
    response_model=PedidoResponse,
    summary="Consulta um pedido por ID",
)
async def get_pedido(
    pedido_id: uuid.UUID,
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PedidoResponse:
    from fastapi import HTTPException

    repo = PedidoRepository(db)
    pedido = await repo.get_by_id(pedido_id)
    if pedido is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pedido não encontrado")
    if pedido.vendedor_id != current_user.vendedor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão")
    return PedidoResponse.model_validate(pedido)


@router.get(
    "",
    response_model=PedidoListResponse,
    summary="Lista pedidos do vendedor autenticado",
)
async def listar_pedidos(
    current_user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PedidoListResponse:
    repo = PedidoRepository(db)
    total, pedidos = await repo.list_by_vendedor(
        vendedor_id=current_user.vendedor_id,
        limit=limit,
        offset=offset,
    )
    return PedidoListResponse(
        total=total,
        items=[PedidoResponse.model_validate(p) for p in pedidos],
    )


@router.patch(
    "/{pedido_id}/cancelar",
    response_model=PedidoResponse,
    summary="Cancela um pedido e libera o estoque reservado",
)
async def cancelar_pedido(
    pedido_id: uuid.UUID,
    current_user: TokenPayload = Depends(get_current_user),
    service: PedidoService = Depends(_make_service),
) -> PedidoResponse:
    pedido = await service.cancelar_pedido(
        pedido_id=pedido_id,
        vendedor_id=current_user.vendedor_id,
    )
    return PedidoResponse.model_validate(pedido)
