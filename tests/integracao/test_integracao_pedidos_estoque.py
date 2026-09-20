"""Integração ponta a ponta: REST do servico-pedidos -> gRPC do servico-estoque.

Cada teste exercita a chamada real entre os dois serviços e confere o efeito no
PostgreSQL do estoque. Nada é mockado: se o contrato, o canal gRPC ou a
orquestração quebrarem, o teste quebra.

Cenários cobertos (o que a issue #7 pede — a chamada Pedidos <-> Estoque):

1. consulta de item pelo contrato (`ConsultarItem`);
2. pedido com **múltiplos itens** — o pedidos chama `CheckAndReserve` uma vez por
   item, com o mesmo `pedido_id`;
3. retry do cliente com a mesma `Idempotency-Key` — não reserva de novo;
4. estoque insuficiente no 2º item — o 1º é **compensado** (`ReleaseReserva`);
5. cancelamento — devolve o estoque de todos os itens;
6. isolamento entre vendedores (o filtro vem do JWT);
7. erros do contrato: `NOT_FOUND` e `INVALID_ARGUMENT`;
8. `ReleaseReserva` idempotente (não devolve o estoque duas vezes);
9. reservas do mesmo item em pedidos diferentes são independentes.
"""

from __future__ import annotations

import uuid

import grpc
import pytest

pytestmark = pytest.mark.integracao


# ---------------------------------------------------------------------------
# 1. ConsultarItem
# ---------------------------------------------------------------------------
def test_consultar_item_pelo_contrato(estoque, stubs, item_factory) -> None:
    modulo, _ = stubs
    item_id = item_factory(quantidade=7, nome="Coxinha")

    resposta = estoque.ConsultarItem(modulo.ItemRequest(item_id=str(item_id)), timeout=10)

    assert resposta.item_id == str(item_id)
    assert resposta.nome == "Coxinha"
    assert resposta.quantidade_disponivel == 7
    assert resposta.quantidade_reservada == 0


# ---------------------------------------------------------------------------
# 2. Pedido com múltiplos itens
# ---------------------------------------------------------------------------
def test_pedido_com_multiplos_itens_reserva_cada_item(
    pedidos, autorizacao, item_factory, estado
) -> None:
    """Um `CheckAndReserve` por item, sempre com o mesmo `pedido_id`."""
    item_a = item_factory(quantidade=10, nome="Coxinha")
    item_b = item_factory(quantidade=5, nome="Pastel")

    resposta = pedidos.post(
        "/pedidos",
        headers=autorizacao,
        json={
            "itens": [
                {"item_id": str(item_a), "quantidade": 2, "preco_unitario": "5.50"},
                {"item_id": str(item_b), "quantidade": 3, "preco_unitario": "7.00"},
            ]
        },
    )

    assert resposta.status_code == 201, resposta.text
    corpo = resposta.json()
    assert corpo["status"] == "CONFIRMADO"
    assert corpo["total"] == "32.00"  # 2*5.50 + 3*7.00, exato em Decimal
    assert len(corpo["itens"]) == 2

    assert estado(item_a) == (8, 2), "item A deveria ter 2 unidades reservadas"
    assert estado(item_b) == (2, 3), "item B deveria ter 3 unidades reservadas"


# ---------------------------------------------------------------------------
# 3. Idempotência do retry (Idempotency-Key)
# ---------------------------------------------------------------------------
def test_retry_com_a_mesma_idempotency_key_nao_reserva_de_novo(
    pedidos, autorizacao, item_factory, estado
) -> None:
    item_id = item_factory(quantidade=4)
    chave = f"integracao-{uuid.uuid4()}"
    corpo = {"itens": [{"item_id": str(item_id), "quantidade": 2, "preco_unitario": "10.00"}]}
    cabecalhos = {**autorizacao, "Idempotency-Key": chave}

    primeira = pedidos.post("/pedidos", headers=cabecalhos, json=corpo)
    segunda = pedidos.post("/pedidos", headers=cabecalhos, json=corpo)

    assert primeira.status_code == 201, primeira.text
    assert segunda.status_code == 201, segunda.text
    assert primeira.json()["id"] == segunda.json()["id"], "o retry deve devolver o MESMO pedido"
    assert estado(item_id) == (2, 2), "o retry não pode reservar estoque de novo"


# ---------------------------------------------------------------------------
# 4. Compensação quando um item posterior falha
# ---------------------------------------------------------------------------
def test_estoque_insuficiente_no_segundo_item_compensa_o_primeiro(
    pedidos, autorizacao, item_factory, estado
) -> None:
    item_ok = item_factory(quantidade=3, nome="Tem estoque")
    item_sem_estoque = item_factory(quantidade=0, nome="Sem estoque")

    resposta = pedidos.post(
        "/pedidos",
        headers=autorizacao,
        json={
            "itens": [
                {"item_id": str(item_ok), "quantidade": 2, "preco_unitario": "1.00"},
                {"item_id": str(item_sem_estoque), "quantidade": 1, "preco_unitario": "1.00"},
            ]
        },
    )

    assert resposta.status_code == 422, resposta.text
    assert "insuficiente" in resposta.json()["detail"].lower()
    assert estado(item_ok) == (3, 0), (
        "a reserva do 1º item precisa ser compensada quando o 2º falha"
    )
    assert estado(item_sem_estoque) == (0, 0)


# ---------------------------------------------------------------------------
# 5. Cancelamento
# ---------------------------------------------------------------------------
def test_cancelamento_devolve_o_estoque_de_todos_os_itens(
    pedidos, autorizacao, item_factory, estado
) -> None:
    item_a = item_factory(quantidade=6, nome="A")
    item_b = item_factory(quantidade=6, nome="B")

    criado = pedidos.post(
        "/pedidos",
        headers=autorizacao,
        json={
            "itens": [
                {"item_id": str(item_a), "quantidade": 1, "preco_unitario": "1.00"},
                {"item_id": str(item_b), "quantidade": 4, "preco_unitario": "1.00"},
            ]
        },
    )
    assert criado.status_code == 201, criado.text
    pedido_id = criado.json()["id"]

    cancelado = pedidos.patch(f"/pedidos/{pedido_id}/cancelar", headers=autorizacao)

    assert cancelado.status_code == 200, cancelado.text
    assert cancelado.json()["status"] == "CANCELADO"
    assert estado(item_a) == (6, 0), "o estoque do item A deveria voltar ao original"
    assert estado(item_b) == (6, 0), "o estoque do item B deveria voltar ao original"


# ---------------------------------------------------------------------------
# 6. Isolamento entre vendedores
# ---------------------------------------------------------------------------
def test_outro_vendedor_nao_ve_o_pedido(
    pedidos, autorizacao, vendedor_id, item_factory, token_de
) -> None:
    item_id = item_factory(quantidade=2)
    criado = pedidos.post(
        "/pedidos",
        headers=autorizacao,
        json={"itens": [{"item_id": str(item_id), "quantidade": 1, "preco_unitario": "1.00"}]},
    )
    assert criado.status_code == 201, criado.text
    pedido_id = criado.json()["id"]

    outro = pedidos.get(f"/pedidos/{pedido_id}", headers=token_de(uuid.uuid4()))
    assert outro.status_code == 403

    meus = pedidos.get("/pedidos", headers=autorizacao)
    assert meus.status_code == 200
    assert all(p["vendedor_id"] == str(vendedor_id) for p in meus.json()["items"])


# ---------------------------------------------------------------------------
# 7. Erros do contrato
# ---------------------------------------------------------------------------
def test_consultar_item_inexistente_aborta_not_found(estoque, stubs) -> None:
    modulo, _ = stubs

    with pytest.raises(grpc.RpcError) as erro:
        estoque.ConsultarItem(modulo.ItemRequest(item_id=str(uuid.uuid4())), timeout=10)

    assert erro.value.code() is grpc.StatusCode.NOT_FOUND


@pytest.mark.parametrize("quantidade", [0, -1])
def test_quantidade_invalida_aborta_invalid_argument(
    estoque, stubs, item_factory, estado, quantidade
) -> None:
    modulo, _ = stubs
    item_id = item_factory(quantidade=5)

    with pytest.raises(grpc.RpcError) as erro:
        estoque.CheckAndReserve(
            modulo.ReservaRequest(
                item_id=str(item_id),
                quantidade=quantidade,
                pedido_id=str(uuid.uuid4()),
                request_uuid=str(uuid.uuid4()),
            ),
            timeout=10,
        )

    assert erro.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert estado(item_id) == (5, 0), "entrada inválida não pode mexer no estoque"


def test_uuid_malformado_aborta_invalid_argument(estoque, stubs) -> None:
    modulo, _ = stubs

    with pytest.raises(grpc.RpcError) as erro:
        estoque.ConsultarItem(modulo.ItemRequest(item_id="nao-e-uuid"), timeout=10)

    assert erro.value.code() is grpc.StatusCode.INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# 8. ReleaseReserva idempotente
# ---------------------------------------------------------------------------
def test_release_repetido_e_idempotente_sem_devolver_o_estoque_duas_vezes(
    estoque, stubs, item_factory, estado
) -> None:
    """O contrato trata liberação repetida como **sucesso idempotente**.

    `JA_APLICADA` conta como sucesso — o estado final desejado (estoque devolvido)
    já foi alcançado — e por isso a resposta vem com `sucesso=True`. O que não pode
    acontecer de jeito nenhum é o estoque ser devolvido duas vezes: é isso que a
    asserção de saldo garante.
    """
    modulo, _ = stubs
    item_id = item_factory(quantidade=5)
    pedido_id = uuid.uuid4()

    reserva = estoque.CheckAndReserve(
        modulo.ReservaRequest(
            item_id=str(item_id),
            quantidade=2,
            pedido_id=str(pedido_id),
            request_uuid=str(uuid.uuid4()),
        ),
        timeout=10,
    )
    assert reserva.sucesso is True
    assert estado(item_id) == (3, 2)

    primeira = estoque.ReleaseReserva(
        modulo.ReleaseRequest(item_id=str(item_id), quantidade=2, pedido_id=str(pedido_id)),
        timeout=10,
    )
    segunda = estoque.ReleaseReserva(
        modulo.ReleaseRequest(item_id=str(item_id), quantidade=2, pedido_id=str(pedido_id)),
        timeout=10,
    )

    assert primeira.sucesso is True
    assert segunda.sucesso is True, "liberação repetida é sucesso idempotente"
    assert "já aplicada" in segunda.mensagem.lower(), (
        "a resposta precisa indicar que a liberação já tinha sido aplicada"
    )
    assert estado(item_id) == (5, 0), "o estoque não pode ser devolvido duas vezes"


def test_release_com_quantidade_divergente_nao_mexe_no_estoque(
    estoque, stubs, item_factory, estado
) -> None:
    """Liberar quantidade diferente da reserva é recusado e não altera o saldo."""
    modulo, _ = stubs
    item_id = item_factory(quantidade=5)
    pedido_id = uuid.uuid4()

    estoque.CheckAndReserve(
        modulo.ReservaRequest(
            item_id=str(item_id),
            quantidade=2,
            pedido_id=str(pedido_id),
            request_uuid=str(uuid.uuid4()),
        ),
        timeout=10,
    )
    assert estado(item_id) == (3, 2)

    divergente = estoque.ReleaseReserva(
        modulo.ReleaseRequest(item_id=str(item_id), quantidade=1, pedido_id=str(pedido_id)),
        timeout=10,
    )

    assert divergente.sucesso is False
    assert estado(item_id) == (3, 2), "liberação divergente não pode mexer no estoque"

    # E a liberação com a quantidade correta continua funcionando depois disso.
    correta = estoque.ReleaseReserva(
        modulo.ReleaseRequest(item_id=str(item_id), quantidade=2, pedido_id=str(pedido_id)),
        timeout=10,
    )
    assert correta.sucesso is True
    assert estado(item_id) == (5, 0)


def test_release_sem_reserva_previa_nao_e_sucesso(estoque, stubs, item_factory, estado) -> None:
    """Liberar o que nunca foi reservado não pode "criar" estoque."""
    modulo, _ = stubs
    item_id = item_factory(quantidade=4)

    resposta = estoque.ReleaseReserva(
        modulo.ReleaseRequest(item_id=str(item_id), quantidade=1, pedido_id=str(uuid.uuid4())),
        timeout=10,
    )

    assert resposta.sucesso is False
    assert estado(item_id) == (4, 0), "não existe reserva para devolver"


# ---------------------------------------------------------------------------
# 9. Reservas independentes por pedido
# ---------------------------------------------------------------------------
def test_mesmo_item_em_pedidos_diferentes_e_independente(
    estoque, stubs, item_factory, estado
) -> None:
    modulo, _ = stubs
    item_id = item_factory(quantidade=2)

    for _ in range(2):
        resposta = estoque.CheckAndReserve(
            modulo.ReservaRequest(
                item_id=str(item_id),
                quantidade=1,
                pedido_id=str(uuid.uuid4()),
                request_uuid=str(uuid.uuid4()),
            ),
            timeout=10,
        )
        assert resposta.sucesso is True

    assert estado(item_id) == (0, 2)

    terceiro = estoque.CheckAndReserve(
        modulo.ReservaRequest(
            item_id=str(item_id),
            quantidade=1,
            pedido_id=str(uuid.uuid4()),
            request_uuid=str(uuid.uuid4()),
        ),
        timeout=10,
    )
    assert terceiro.sucesso is False
    assert terceiro.status == "ESTOQUE_INSUFICIENTE"
