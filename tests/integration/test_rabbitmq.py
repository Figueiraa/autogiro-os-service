"""Mensageria contra um RabbitMQ de verdade.

Roda quando `TEST_RABBITMQ_URL` está definida (no CI, um container do RabbitMQ;
localmente, o do docker-compose). Sem ela, os testes são pulados.
"""

import asyncio
import json
import os
import uuid

import aio_pika
import pytest
from sqlalchemy import select

from app.application.messages import EXCHANGE_COMMANDS, EXCHANGE_EVENTS, Envelope
from app.infrastructure.config import settings
from app.infrastructure.messaging.consumer import ERROR_HEADER, RETRY_HEADER, QueueConsumer
from app.infrastructure.messaging.rabbitmq import QUEUE_SAGA_EVENTS, RabbitMQClient, dead_letter_queue
from app.infrastructure.messaging.runtime import MessagingRuntime, publish_pending
from app.infrastructure.persistence.models.saga_model import OutboxMessage
from app.infrastructure.persistence.repositories.saga_repository import SqlAlchemySagaRepository

RABBITMQ_URL = os.getenv("TEST_RABBITMQ_URL")

pytestmark = pytest.mark.skipif(not RABBITMQ_URL, reason="TEST_RABBITMQ_URL não definida")


@pytest.fixture
async def canal():
    conexao = await aio_pika.connect_robust(RABBITMQ_URL)
    canal = await conexao.channel()
    yield canal
    await conexao.close()


async def _esperar_mensagem(fila, segundos: float = 5.0):
    for _ in range(int(segundos / 0.1)):
        mensagem = await fila.get(no_ack=True, fail=False)
        if mensagem is not None:
            return mensagem
        await asyncio.sleep(0.1)
    return None


async def _abrir_os(auth_client) -> dict:
    cliente = await auth_client.post("/api/v1/clients", json={"name": "Maria", "cpf_cnpj": "529.982.247-25"})
    veiculo = await auth_client.post(
        "/api/v1/vehicles",
        json={"client_id": cliente.json()["id"], "plate": "ABC1D23", "brand": "Fiat", "model": "Argo", "year": 2021},
    )
    servico = await auth_client.post("/api/v1/service-types", json={"name": "Troca de óleo", "price": 149.9})
    r = await auth_client.post(
        "/api/v1/service-orders",
        json={
            "vehicle_id": veiculo.json()["id"],
            "items": [{"service_type_id": servico.json()["id"]}],
            "parts": [{"part_id": 3, "quantity": 1}],
        },
    )
    return r.json()


async def test_comando_do_outbox_chega_ao_exchange_de_comandos(auth_client, session_factory, canal):
    cliente = RabbitMQClient(RABBITMQ_URL)
    await cliente.connect()
    await cliente.declare_exchanges()
    fila = await canal.declare_queue(exclusive=True)
    await fila.bind(EXCHANGE_COMMANDS, routing_key="execution.reservar-pecas")
    ordem = await _abrir_os(auth_client)

    assert await publish_pending(session_factory, cliente.publish) == 1
    mensagem = await _esperar_mensagem(fila)
    await cliente.close()

    assert mensagem is not None
    assert (mensagem.type, mensagem.content_type) == ("ReservarPecas", "application/json")
    assert mensagem.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert json.loads(mensagem.body)["order_id"] == ordem["id"]


async def test_evento_publicado_e_processado_pela_saga(auth_client, session_factory, db, canal, monkeypatch):
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 3600)
    monkeypatch.setattr(settings, "SAGA_STALL_CHECK_SECONDS", 3600)
    await canal.queue_delete(QUEUE_SAGA_EVENTS)
    ordem = await _abrir_os(auth_client)
    saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])

    runtime = MessagingRuntime(session_factory, RabbitMQClient(RABBITMQ_URL))
    await runtime.start()
    await asyncio.sleep(0.5)  # deixa as tarefas periódicas fazerem a primeira rodada
    evento = Envelope(
        type="PecasReservadas",
        kind="event",
        saga_id=saga.id,
        order_id=ordem["id"],
        producer="execution-service",
        payload={"items": [{"part_id": 3, "name": "Filtro", "quantity": 1, "unit_price": 30}]},
    )
    exchange = await canal.get_exchange(EXCHANGE_EVENTS)
    await exchange.publish(aio_pika.Message(evento.to_json().encode()), routing_key=evento.routing_key)

    comandos = []
    for _ in range(50):
        async with session_factory() as sessao:
            comandos = (await sessao.execute(select(OutboxMessage.message_type))).scalars().all()
        if "GerarOrcamento" in comandos:
            break
        await asyncio.sleep(0.1)
    assert await runtime.is_up()
    await runtime.stop()

    assert "GerarOrcamento" in comandos


async def test_mensagem_invalida_vai_para_a_dlq(canal, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 3600)
    monkeypatch.setattr(settings, "SAGA_STALL_CHECK_SECONDS", 3600)
    await canal.queue_delete(QUEUE_SAGA_EVENTS)
    await canal.queue_delete(dead_letter_queue(QUEUE_SAGA_EVENTS))
    runtime = MessagingRuntime(session_factory, RabbitMQClient(RABBITMQ_URL))
    await runtime.start()

    exchange = await canal.get_exchange(EXCHANGE_EVENTS)
    await exchange.publish(aio_pika.Message(b"{isto nao e um envelope"), routing_key="execution.qualquer-coisa")
    dlq = await canal.declare_queue(dead_letter_queue(QUEUE_SAGA_EVENTS), durable=True)
    mensagem = await _esperar_mensagem(dlq)
    await runtime.stop()

    assert mensagem is not None
    assert "Envelope inválido" in mensagem.headers[ERROR_HEADER]


async def test_falha_tecnica_passa_pelo_retry_e_termina_na_dlq(canal):
    fila_nome = f"teste.falhas.{uuid.uuid4().hex[:8]}"
    cliente = RabbitMQClient(RABBITMQ_URL, retry_delay_ms=200)
    await cliente.connect()
    await cliente.declare_exchanges()
    fila = await cliente.declare_queue_with_retry(fila_nome, EXCHANGE_EVENTS, (f"{fila_nome}.#",))
    tentativas = []

    async def sempre_falha(envelope):
        tentativas.append(envelope.message_id)
        raise ConnectionError("banco fora do ar")

    await fila.consume(QueueConsumer(cliente, fila_nome, sempre_falha, max_retries=1))
    evento = Envelope(type="X", kind="event", saga_id="s", order_id=1)
    await cliente.publish("", fila_nome, evento.to_json().encode(), message_id=evento.message_id, message_type="X")

    dlq = await canal.declare_queue(dead_letter_queue(fila_nome), durable=True)
    mensagem = await _esperar_mensagem(dlq)
    await cliente.close()
    for nome in (fila_nome, f"{fila_nome}.retry", f"{fila_nome}.dlq"):
        await canal.queue_delete(nome)

    assert mensagem is not None
    assert len(tentativas) == 2  # a original e uma nova tentativa depois do atraso
    assert mensagem.headers[RETRY_HEADER] == 1
    assert mensagem.headers[ERROR_HEADER] == "banco fora do ar"


async def test_api_sobe_com_a_mensageria_e_a_prontidao_inclui_o_broker(monkeypatch):
    from app.main import create_app, lifespan

    monkeypatch.setattr(settings, "RABBITMQ_URL", RABBITMQ_URL)
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 3600)
    monkeypatch.setattr(settings, "SAGA_STALL_CHECK_SECONDS", 3600)
    app = create_app()

    async with lifespan(app):
        assert await app.state.readiness_checks["broker"]() is True
