"""Tarefas de mensageria sem broker: outbox, consumo e laço periódico."""

import asyncio
import json

from sqlalchemy import select

from app.application.messages import Envelope
from app.infrastructure.messaging.consumer import Outcome, process
from app.infrastructure.messaging.runtime import handle_with, publish_pending, retry_stalled, run_every
from app.infrastructure.observability import metrics
from app.infrastructure.persistence.models.saga_model import OutboxMessage, ProcessedMessage
from app.infrastructure.persistence.repositories.saga_repository import (
    SqlAlchemyOutbox,
    SqlAlchemySagaRepository,
)


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


async def _abrir_os_de_outro_cliente(auth_client) -> dict:
    cliente = await auth_client.post("/api/v1/clients", json={"name": "João", "cpf_cnpj": "111.444.777-35"})
    veiculo = await auth_client.post(
        "/api/v1/vehicles",
        json={"client_id": cliente.json()["id"], "plate": "XYZ9K87", "brand": "VW", "model": "Gol", "year": 2015},
    )
    servico = await auth_client.post("/api/v1/service-types", json={"name": "Alinhamento", "price": 80.0})
    r = await auth_client.post(
        "/api/v1/service-orders",
        json={"vehicle_id": veiculo.json()["id"], "items": [{"service_type_id": servico.json()["id"]}]},
    )
    return r.json()


class PublicadorFalso:
    def __init__(self, falhar=False):
        self.publicadas = []
        self.cabecalhos = []
        self.falhar = falhar

    async def __call__(self, exchange, routing_key, body, *, message_id, message_type, headers=None):
        if self.falhar:
            raise ConnectionError("broker fora do ar")
        self.publicadas.append((exchange, routing_key, json.loads(body), message_type))
        self.cabecalhos.append(headers)


class TestOutbox:
    async def test_publica_as_pendentes_uma_vez(self, auth_client, session_factory):
        ordem = await _abrir_os(auth_client)
        publicador = PublicadorFalso()

        assert await publish_pending(session_factory, publicador) == 1
        assert await publish_pending(session_factory, publicador) == 0

        exchange, rota, corpo, tipo = publicador.publicadas[0]
        assert (exchange, rota, tipo) == ("autogiro.commands", "execution.reservar-pecas", "ReservarPecas")
        assert corpo["order_id"] == ordem["id"]

    async def test_publica_com_o_trace_em_que_a_mensagem_foi_criada(self, auth_client, session_factory, db):
        await _abrir_os(auth_client)
        traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        linha = (await db.execute(select(OutboxMessage))).scalar_one()
        linha.trace_context = json.dumps({"traceparent": traceparent})
        await db.commit()
        publicador = PublicadorFalso()

        await publish_pending(session_factory, publicador)

        # O trace da requisição continua no cabeçalho da mensagem publicada.
        assert "0af7651916cd43dd8448eb211c80319c" in publicador.cabecalhos[0]["traceparent"]

    async def test_trava_e_publica_uma_mensagem_por_transacao(self, auth_client, session_factory, monkeypatch):
        # Com o lote inteiro travado, o commit da primeira soltaria a trava das demais
        # e outra réplica as publicaria de novo.
        await _abrir_os(auth_client)
        await _abrir_os_de_outro_cliente(auth_client)
        leituras = []
        pendentes = SqlAlchemyOutbox.pending

        async def pendentes_espionado(outbox, limit=100):
            linhas = await pendentes(outbox, limit)
            leituras.append((limit, len(linhas)))
            return linhas

        monkeypatch.setattr(SqlAlchemyOutbox, "pending", pendentes_espionado)
        publicador = PublicadorFalso()

        assert await publish_pending(session_factory, publicador) == 2

        assert leituras == [(1, 1), (1, 1), (1, 0)]
        assert [p[3] for p in publicador.publicadas] == ["ReservarPecas", "ReservarPecas"]

    async def test_respeita_o_limite_por_rodada(self, auth_client, session_factory):
        await _abrir_os(auth_client)
        await _abrir_os_de_outro_cliente(auth_client)

        assert await publish_pending(session_factory, PublicadorFalso(), limit=1) == 1
        assert await publish_pending(session_factory, PublicadorFalso(), limit=1) == 1
        assert await publish_pending(session_factory, PublicadorFalso(), limit=1) == 0

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

    async def test_evento_adiantado_vai_para_a_fila_de_retry(self, auth_client, session_factory, db):
        ordem = await _abrir_os(auth_client)
        saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])
        adiantado = Envelope(
            type="OrcamentoAprovado", kind="event", saga_id=saga.id, order_id=ordem["id"], payload={}
        )

        entrega = await process(adiantado.to_json().encode(), {}, handle_with(session_factory), max_retries=3)

        assert (entrega.outcome, entrega.retry_count) == (Outcome.RETRY, 1)
        assert "fora de ordem" in entrega.error
        assert await db.get(ProcessedMessage, adiantado.message_id) is None

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
