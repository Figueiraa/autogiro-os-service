"""Tarefas de mensageria sem broker: outbox, consumo e laço periódico."""

import asyncio
import json

from sqlalchemy import select

from app.application.messages import Envelope
from app.infrastructure.messaging.runtime import handle_with, publish_pending, retry_stalled, run_every
from app.infrastructure.observability import metrics
from app.infrastructure.persistence.models.saga_model import OutboxMessage
from app.infrastructure.persistence.repositories.saga_repository import SqlAlchemySagaRepository


async def _abrir_os(auth_client) -> dict:
    cliente = await auth_client.post("/api/v1/clients", json={"name": "Maria", "cpf_cnpj": "529.982.247-25"})
    veiculo = await auth_client.post(
        "/api/v1/vehicles",
        json={"client_id": cliente.json()["id"], "plate": "ABC1D23", "brand": "Fiat", "model": "Argo", "year": 2021},
    )
    servico = await auth_client.post("/api/v1/service-types", json={"name": "Troca de óleo", "price": 149.9})
    r = await auth_client.post(
        "/api/v1/service-orders",
        json={"vehicle_id": veiculo.json()["id"], "items": [{"service_type_id": servico.json()["id"]}]},
    )
    return r.json()


class PublicadorFalso:
    def __init__(self, falhar=False):
        self.publicadas = []
        self.falhar = falhar

    async def __call__(self, exchange, routing_key, body, *, message_id, message_type):
        if self.falhar:
            raise ConnectionError("broker fora do ar")
        self.publicadas.append((exchange, routing_key, json.loads(body), message_type))


class TestOutbox:
    async def test_publica_as_pendentes_uma_vez(self, auth_client, session_factory):
        ordem = await _abrir_os(auth_client)
        publicador = PublicadorFalso()

        assert await publish_pending(session_factory, publicador) == 1
        assert await publish_pending(session_factory, publicador) == 0

        exchange, rota, corpo, tipo = publicador.publicadas[0]
        assert (exchange, rota, tipo) == ("autogiro.commands", "execution.reservar-pecas", "ReservarPecas")
        assert corpo["order_id"] == ordem["id"]

    async def test_mensagem_continua_pendente_se_o_broker_falhar(self, auth_client, session_factory, db):
        await _abrir_os(auth_client)

        try:
            await publish_pending(session_factory, PublicadorFalso(falhar=True))
        except ConnectionError:
            pass

        pendentes = (await db.execute(select(OutboxMessage).where(OutboxMessage.published_at.is_(None)))).scalars()
        assert len(list(pendentes)) == 1


class TestConsumo:
    async def test_processa_o_evento_e_registra_metricas_das_transicoes(self, auth_client, session_factory, db):
        ordem = await _abrir_os(auth_client)
        saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])
        handle = handle_with(session_factory)
        contador = metrics.service_order_status_transitions_total.labels(status="AGUARDANDO_APROVACAO")
        antes = contador._value.get()

        for tipo, payload in (("PecasReservadas", {"items": []}), ("OrcamentoGerado", {"total": 149.9})):
            await handle(Envelope(type=tipo, kind="event", saga_id=saga.id, order_id=ordem["id"], payload=payload))

        assert contador._value.get() == antes + 1

    async def test_reenvio_periodico_usa_sessao_propria(self, auth_client, session_factory):
        await _abrir_os(auth_client)
        assert await retry_stalled(session_factory) == 0


class TestLacoPeriodico:
    async def test_continua_rodando_mesmo_quando_a_tarefa_falha(self):
        execucoes = []
        segunda_execucao = asyncio.Event()

        async def tarefa():
            execucoes.append(1)
            if len(execucoes) >= 2:
                segunda_execucao.set()
            raise RuntimeError("falha temporária")

        laco = asyncio.create_task(run_every(0.01, tarefa, "teste"))
        await asyncio.wait_for(segunda_execucao.wait(), timeout=5)
        laco.cancel()
        await asyncio.gather(laco, return_exceptions=True)

        assert len(execucoes) >= 2
