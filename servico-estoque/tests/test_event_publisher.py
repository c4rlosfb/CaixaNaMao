"""Contrato de publicação de eventos: best-effort e FORA do caminho crítico.

Regressão coberta aqui: o handler aguardava o envio ao SQS (`await asyncio.to_thread(...)`).
Com um endpoint de mensageria indisponível, cada reserva passava a levar ~7s e o
cliente gRPC (deadline de 5s) recebia DEADLINE_EXCEEDED — a operação de estoque
acontecia, mas o comando falhava para quem chamou.
"""

from __future__ import annotations

import asyncio
import time

from app.clients.event_publisher import NullEventPublisher, SQSEventPublisher


def _evento() -> dict[str, object]:
    return {"event_id": "evt-1", "evento": "EstoqueAtualizado"}


async def test_publicacao_nao_bloqueia_o_caminho_critico() -> None:
    """`publicar_estoque_atualizado` agenda e retorna; o envio ocorre em background."""
    publicador = SQSEventPublisher(queue_name="fila-teste")
    enviados: list[dict[str, object]] = []

    def _envio_lento(evento: dict[str, object]) -> None:
        time.sleep(2.0)  # simula broker lento/indisponível
        enviados.append(evento)

    publicador._enviar_e_logar = _envio_lento  # type: ignore[method-assign]

    inicio = time.perf_counter()
    await publicador.publicar_estoque_atualizado(evento=_evento())
    duracao = time.perf_counter() - inicio

    assert duracao < 0.5, f"publicação bloqueou o handler por {duracao:.2f}s"

    await publicador.aguardar_publicacoes()
    assert enviados == [_evento()]


async def test_falha_de_publicacao_nao_propaga_nem_derruba_o_comando() -> None:
    publicador = SQSEventPublisher(queue_name="fila-teste")

    def _falha(evento: dict[str, object]) -> None:
        raise RuntimeError("broker indisponível")

    publicador._enviar_e_logar = _falha  # type: ignore[method-assign]

    # Não levanta — best-effort (ADR-005)
    await publicador.publicar_estoque_atualizado(evento=_evento())
    await publicador.aguardar_publicacoes()


async def test_publicacoes_sao_aguardaveis() -> None:
    publicador = SQSEventPublisher(queue_name="fila-teste")
    ordem: list[int] = []

    def _envio(evento: dict[str, object]) -> None:
        time.sleep(0.05)
        ordem.append(int(str(evento["event_id"]).split("-")[-1]))

    publicador._enviar_e_logar = _envio  # type: ignore[method-assign]

    for i in range(3):
        await publicador.publicar_estoque_atualizado(evento={"event_id": f"evt-{i}"})

    inicio = time.perf_counter()
    await publicador.aguardar_publicacoes()

    assert len(ordem) == 3
    assert time.perf_counter() - inicio < 2.0
    assert publicador._tarefas == set()


async def test_null_publisher_nao_faz_nada() -> None:
    publicador = NullEventPublisher()

    await publicador.publicar_estoque_atualizado(evento=_evento())


async def test_publicacao_em_lote_nao_serializa_espera() -> None:
    """10 publicações agendadas custam ~o tempo de uma (não somam latência)."""
    publicador = SQSEventPublisher(queue_name="fila-teste")

    def _envio_lento(evento: dict[str, object]) -> None:
        time.sleep(1.0)

    publicador._enviar_e_logar = _envio_lento  # type: ignore[method-assign]

    inicio = time.perf_counter()
    for i in range(10):
        await publicador.publicar_estoque_atualizado(evento={"event_id": f"evt-{i}"})
    duracao = time.perf_counter() - inicio

    assert duracao < 0.5, f"10 publicações custaram {duracao:.2f}s no handler"
    await publicador.aguardar_publicacoes()


def test_tarefas_sao_registradas_para_nao_serem_coletadas_pelo_gc() -> None:
    async def cenario() -> None:
        publicador = SQSEventPublisher(queue_name="fila-teste")

        def _envio_lento(evento: dict[str, object]) -> None:
            time.sleep(0.2)

        publicador._enviar_e_logar = _envio_lento  # type: ignore[method-assign]
        await publicador.publicar_estoque_atualizado(evento=_evento())
        assert len(publicador._tarefas) == 1
        await publicador.aguardar_publicacoes()
        assert len(publicador._tarefas) == 0

    asyncio.run(cenario())
