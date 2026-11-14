"""Orquestrador da Saga sobre o banco: abertura, eventos, compensações e reenvio."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.application.messages import Envelope
from app.application.use_cases.saga_orchestrator import APPLIED, DUPLICATE, IGNORED
from app.domain.entities.saga import SagaStep
from app.domain.exceptions import InvalidStatusTransitionError, OutOfOrderEventError
from app.domain.value_objects.service_order_status import ServiceOrderStatus
from app.infrastructure.composition import build_orchestrator
from app.infrastructure.persistence.models.saga_model import OutboxMessage, ProcessedMessage
from app.infrastructure.persistence.repositories.saga_repository import SqlAlchemySagaRepository
from app.infrastructure.persistence.repositories.service_order_repository import (
    SqlAlchemyServiceOrderRepository,
)


class NotificadorFalso:
    def __init__(self):
        self.enviadas: list[tuple[str | None, str, str]] = []

    async def notify_status_change(self, recipient, order_number, new_status):
        self.enviadas.append((recipient, order_number, new_status))


async def _abrir_os(auth_client, parts=None) -> dict:
    cliente = await auth_client.post(
        "/api/v1/clients", json={"name": "Maria", "cpf_cnpj": "529.982.247-25", "email": "maria@exemplo.com"}
    )
    veiculo = await auth_client.post(
        "/api/v1/vehicles",
        json={"client_id": cliente.json()["id"], "plate": "ABC1D23", "brand": "Fiat", "model": "Argo", "year": 2021},
    )
    servico = await auth_client.post("/api/v1/service-types", json={"name": "Troca de óleo", "price": 149.9})
    r = await auth_client.post(
        "/api/v1/service-orders",
        json={
            "vehicle_id": veiculo.json()["id"],
            "notes": "barulho",
            "items": [{"service_type_id": servico.json()["id"], "quantity": 1}],
            "parts": parts if parts is not None else [{"part_id": 3, "quantity": 2}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _saga_id(db, order_id: int) -> str:
    return (await SqlAlchemySagaRepository(db).get_by_order(order_id)).id


def _evento(tipo, saga_id, order_id, payload=None, produtor="execution-service") -> Envelope:
    return Envelope(type=tipo, kind="event", saga_id=saga_id, order_id=order_id, payload=payload or {}, producer=produtor)


async def _comandos(db) -> list[tuple[str, dict]]:
    linhas = (await db.execute(select(OutboxMessage).order_by(OutboxMessage.id))).scalars().all()
    return [(linha.message_type, json.loads(linha.body)) for linha in linhas]


async def _os(db, order_id):
    return await SqlAlchemyServiceOrderRepository(db).get_by_id(order_id)


CAMINHO = [
    ("PecasReservadas", {"items": [{"part_id": 3, "name": "Filtro de óleo", "quantity": 2, "unit_price": 32.5}]}),
    ("OrcamentoGerado", {"budget_id": 1, "total": 214.9}),
    ("OrcamentoAprovado", {"budget_id": 1, "payment_id": 1, "payment_link": "https://mp.test/pagar/1", "amount": 214.9}),
    ("PagamentoConfirmado", {"payment_id": 1, "mp_payment_id": "999", "amount": 214.9}),
    ("ExecucaoEnfileirada", {"position": 1}),
]


async def _avancar(db, ordem, eventos):
    orquestrador = build_orchestrator(db, notify=False)
    saga_id = await _saga_id(db, ordem["id"])
    for tipo, payload in eventos:
        resultado = await orquestrador.handle(_evento(tipo, saga_id, ordem["id"], payload))
        assert resultado.outcome == APPLIED, (tipo, resultado.note)
    return orquestrador, saga_id


class TestInicio:
    async def test_abrir_a_os_inicia_a_saga_com_a_reserva_de_pecas(self, auth_client, db):
        ordem = await _abrir_os(auth_client)

        comandos = await _comandos(db)
        assert [tipo for tipo, _ in comandos] == ["ReservarPecas"]
        corpo = comandos[0][1]
        assert corpo["payload"] == {"items": [{"part_id": 3, "quantity": 2}]}
        assert (corpo["order_id"], corpo["kind"], corpo["causation_id"]) == (ordem["id"], "command", None)

        r = await auth_client.get(f"/api/v1/service-orders/{ordem['id']}/saga")
        assert r.status_code == 200
        saga = r.json()
        assert (saga["state"], saga["current_step"]) == ("EM_ANDAMENTO", "RESERVAR_PECAS")
        assert saga["steps"] == [
            {
                "step": "RESERVAR_PECAS",
                "status": "PENDENTE",
                "command": "ReservarPecas",
                "attempts": 1,
                "last_sent_at": saga["steps"][0]["last_sent_at"],
                "finished_at": None,
            }
        ]

    async def test_saga_de_os_inexistente_responde_404(self, auth_client):
        assert (await auth_client.get("/api/v1/service-orders/999/saga")).status_code == 404


class TestCaminhoFeliz:
    async def test_da_reserva_a_finalizacao(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO)
        for tipo in ("DiagnosticoIniciado", "ReparoIniciado", "ExecucaoFinalizada"):
            await orquestrador.handle(_evento(tipo, saga_id, ordem["id"]))

        os_final = await _os(db, ordem["id"])
        assert os_final.status.value == "FINALIZADA"
        assert (os_final.budget_total, os_final.payment_link) == (214.9, "https://mp.test/pagar/1")
        assert (os_final.parts[0].name, os_final.parts[0].unit_price) == ("Filtro de óleo", 32.5)
        assert [h.source for h in os_final.history][-3:] == ["DiagnosticoIniciado", "ReparoIniciado", "ExecucaoFinalizada"]

        saga = (await auth_client.get(f"/api/v1/service-orders/{ordem['id']}/saga")).json()
        assert saga["state"] == "CONCLUIDA"
        assert [tipo for tipo, _ in await _comandos(db)] == ["ReservarPecas", "GerarOrcamento", "EnfileirarExecucao"]

    async def test_comandos_levam_os_dados_que_o_participante_precisa(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        await _avancar(db, ordem, CAMINHO[:4])

        comandos = dict(await _comandos(db))
        orcamento = comandos["GerarOrcamento"]["payload"]
        assert orcamento["client"] == {"id": ordem["client_id"], "name": "Maria", "email": "maria@exemplo.com"}
        assert orcamento["services"] == [
            {"service_type_id": ordem["items"][0]["service_type_id"], "name": "Troca de óleo", "quantity": 1, "unit_price": 149.9}
        ]
        assert orcamento["parts"] == [{"part_id": 3, "name": "Filtro de óleo", "quantity": 2, "unit_price": 32.5}]

        fila = comandos["EnfileirarExecucao"]["payload"]
        assert fila["vehicle"] == {"plate": "ABC1D23", "brand": "Fiat", "model": "Argo"}
        assert fila["notes"] == "barulho"
        assert fila["parts"] == [{"part_id": 3, "name": "Filtro de óleo", "quantity": 2}]

    async def test_comando_referencia_o_evento_que_o_causou(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador = build_orchestrator(db, notify=False)
        evento = _evento("PecasReservadas", await _saga_id(db, ordem["id"]), ordem["id"], CAMINHO[0][1])

        await orquestrador.handle(evento)

        assert (await _comandos(db))[-1][1]["causation_id"] == evento.message_id


class TestCompensacao:
    async def test_falha_depois_do_pagamento_estorna_e_libera_as_pecas(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO[:4])

        resultado = await orquestrador.handle(
            _evento("EnfileiramentoRecusado", saga_id, ordem["id"], {"reason": "FILA_CHEIA"})
        )

        assert resultado.commands == ["LiberarPecas", "CancelarCobranca"]
        os_cancelando = await _os(db, ordem["id"])
        assert (os_cancelando.status.value, os_cancelando.cancel_reason) == ("EM_CANCELAMENTO", "EXECUCAO_RECUSADA")
        compensacoes = (await _comandos(db))[-2:]
        assert [c[1]["payload"] for c in compensacoes] == [{"reason": "EXECUCAO_RECUSADA"}] * 2

        await orquestrador.handle(_evento("PecasLiberadas", saga_id, ordem["id"], {"released": True}))
        assert (await _os(db, ordem["id"])).status.value == "EM_CANCELAMENTO"
        await orquestrador.handle(
            _evento("CobrancaCancelada", saga_id, ordem["id"], {"estornado": True}, produtor="billing-service")
        )

        assert (await _os(db, ordem["id"])).status.value == "CANCELADA"
        saga = (await auth_client.get(f"/api/v1/service-orders/{ordem['id']}/saga")).json()
        assert (saga["state"], saga["failure_reason"]) == ("COMPENSADA", "EXECUCAO_RECUSADA")

    async def test_peca_indisponivel_cancela_sem_compensacoes(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador = build_orchestrator(db, notify=False)

        await orquestrador.handle(
            _evento("PecasIndisponiveis", await _saga_id(db, ordem["id"]), ordem["id"], {"missing": [{"part_id": 3}]})
        )

        os_final = await _os(db, ordem["id"])
        assert (os_final.status.value, os_final.cancel_reason) == ("CANCELADA", "PECAS_INDISPONIVEIS")
        assert [tipo for tipo, _ in await _comandos(db)] == ["ReservarPecas"]


class TestRobustez:
    async def test_mensagem_repetida_nao_tem_efeito(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador = build_orchestrator(db, notify=False)
        evento = _evento("PecasReservadas", await _saga_id(db, ordem["id"]), ordem["id"], CAMINHO[0][1])

        assert (await orquestrador.handle(evento)).outcome == APPLIED
        assert (await orquestrador.handle(evento)).outcome == DUPLICATE
        assert [tipo for tipo, _ in await _comandos(db)] == ["ReservarPecas", "GerarOrcamento"]

    async def test_evento_de_outra_saga_e_ignorado_e_registrado(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        evento = _evento("PecasReservadas", "saga-que-nao-existe", ordem["id"])

        resultado = await build_orchestrator(db, notify=False).handle(evento)

        assert resultado.outcome == IGNORED
        assert await db.get(ProcessedMessage, evento.message_id) is not None

    async def test_comando_no_lugar_de_evento_e_ignorado(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        comando = Envelope.command(
            "ReservarPecas", saga_id=await _saga_id(db, ordem["id"]), order_id=ordem["id"], payload={}
        )
        assert (await build_orchestrator(db, notify=False).handle(comando)).outcome == IGNORED

    async def test_evento_adiantado_nao_muda_a_os_nem_e_marcado_como_processado(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        evento = _evento("PagamentoConfirmado", await _saga_id(db, ordem["id"]), ordem["id"])

        with pytest.raises(OutOfOrderEventError):
            await build_orchestrator(db, notify=False).handle(evento)

        assert (await _os(db, ordem["id"])).status.value == "RECEBIDA"
        # Sem registro no inbox: quando voltar da fila de retry, ele será processado.
        assert await db.get(ProcessedMessage, evento.message_id) is None

    async def test_evento_de_etapa_ja_concluida_e_ignorado_e_registrado(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO[:2])
        atrasado = _evento("PecasReservadas", saga_id, ordem["id"], CAMINHO[0][1])

        resultado = await orquestrador.handle(atrasado)

        assert resultado.outcome == IGNORED
        assert await db.get(ProcessedMessage, atrasado.message_id) is not None

    async def test_progresso_repetido_da_execucao_e_ignorado_na_os(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO)
        await orquestrador.handle(_evento("DiagnosticoIniciado", saga_id, ordem["id"]))

        resultado = await orquestrador.handle(_evento("DiagnosticoIniciado", saga_id, ordem["id"]))

        assert (resultado.outcome, resultado.transitions) == (IGNORED, [])
        assert (await _os(db, ordem["id"])).status.value == "EM_DIAGNOSTICO"

    async def test_transicao_invalida_nao_e_engolida(self, auth_client, db):
        # OS e Saga em desacordo (OS voltou a RECEBIDA): nada é gravado e o evento
        # segue para retry/DLQ em vez de sumir com a Saga avançada e a OS parada.
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO[:2])
        repositorio = SqlAlchemyServiceOrderRepository(db)
        os_ = await repositorio.get_by_id(ordem["id"])
        os_.status = ServiceOrderStatus.RECEBIDA
        await repositorio.update(os_)
        evento = _evento("OrcamentoAprovado", saga_id, ordem["id"], CAMINHO[2][1])

        with pytest.raises(InvalidStatusTransitionError):
            await orquestrador.handle(evento)

        saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])
        assert saga.current_step.value == "AGUARDAR_APROVACAO"
        assert await db.get(ProcessedMessage, evento.message_id) is None


class TestEventosForaDeOrdem:
    """Consumo paralelo (réplicas, prefetch, fila de retry): um evento pode chegar antes
    do que o precede. Ele falha, volta pela fila de retry e é aplicado na vez certa."""

    async def test_aprovacao_antes_do_orcamento_volta_e_e_aplicada_depois(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO[:1])
        aprovado = _evento("OrcamentoAprovado", saga_id, ordem["id"], CAMINHO[2][1], "billing-service")
        pago = _evento("PagamentoConfirmado", saga_id, ordem["id"], CAMINHO[3][1], "billing-service")

        with pytest.raises(OutOfOrderEventError):
            await orquestrador.handle(aprovado)
        with pytest.raises(OutOfOrderEventError):
            await orquestrador.handle(pago)
        gerado = _evento("OrcamentoGerado", saga_id, ordem["id"], CAMINHO[1][1], "billing-service")
        assert (await orquestrador.handle(gerado)).outcome == APPLIED
        # As mesmas mensagens, de volta da fila de retry.
        assert (await orquestrador.handle(aprovado)).outcome == APPLIED
        assert (await orquestrador.handle(pago)).outcome == APPLIED

        os_ = await _os(db, ordem["id"])
        assert (os_.status.value, os_.payment_link) == ("PAGA", "https://mp.test/pagar/1")
        saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])
        assert saga.current_step.value == "ENFILEIRAR_EXECUCAO"

    async def test_finalizacao_antes_do_reparo_nao_prende_a_os(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO)
        finalizada = _evento("ExecucaoFinalizada", saga_id, ordem["id"])

        assert (await orquestrador.handle(_evento("DiagnosticoIniciado", saga_id, ordem["id"]))).outcome == APPLIED
        with pytest.raises(OutOfOrderEventError):
            await orquestrador.handle(finalizada)
        saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])
        assert saga.state.value == "EM_ANDAMENTO"

        assert (await orquestrador.handle(_evento("ReparoIniciado", saga_id, ordem["id"]))).outcome == APPLIED
        assert (await orquestrador.handle(finalizada)).outcome == APPLIED

        assert (await _os(db, ordem["id"])).status.value == "FINALIZADA"
        assert (await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])).state.value == "CONCLUIDA"
        assert (await auth_client.post(f"/api/v1/service-orders/{ordem['id']}/deliver")).status_code == 200

    async def test_pagamento_tardio_continua_pedindo_estorno(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador, saga_id = await _avancar(db, ordem, CAMINHO[:3])
        await orquestrador.handle(_evento("PagamentoExpirado", saga_id, ordem["id"], produtor="billing-service"))

        resultado = await orquestrador.handle(
            _evento("PagamentoConfirmado", saga_id, ordem["id"], CAMINHO[3][1], "billing-service")
        )

        assert (resultado.outcome, resultado.commands) == (APPLIED, ["CancelarCobranca"])


class TestReenvio:
    async def test_reenvia_comando_sem_resposta(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador = build_orchestrator(db, notify=False)
        daqui_a_pouco = datetime.now(UTC) + timedelta(minutes=3)

        assert await orquestrador.retry_stalled(daqui_a_pouco) == 1
        assert await orquestrador.retry_stalled(daqui_a_pouco) == 0

        comandos = await _comandos(db)
        assert [tipo for tipo, _ in comandos] == ["ReservarPecas", "ReservarPecas"]
        assert comandos[0][1]["message_id"] != comandos[1][1]["message_id"]
        saga = (await auth_client.get(f"/api/v1/service-orders/{ordem['id']}/saga")).json()
        assert saga["steps"][0]["attempts"] == 2

    async def test_evento_processado_durante_o_reenvio_nao_e_desfeito(self, auth_client, db, session_factory):
        ordem = await _abrir_os(auth_client)
        agora = datetime.now(UTC)
        # A reserva de peças foi pedida há 10 minutos e ficou sem resposta.
        sagas = SqlAlchemySagaRepository(db)
        saga = await sagas.get_by_order(ordem["id"])
        saga.steps[SagaStep.RESERVAR_PECAS].last_sent_at = agora - timedelta(minutes=10)
        await sagas.save(saga)
        await db.commit()

        async with session_factory() as sessao_do_reenvio:
            reenvio = build_orchestrator(sessao_do_reenvio, notify=False)
            listar = reenvio._sagas.list_running

            async def listar_e_chegar_o_evento():
                # Outro pod processa PecasReservadas logo depois da listagem do reenvio.
                fotos = await listar()
                await _avancar(db, ordem, CAMINHO[:1])
                return fotos

            reenvio._sagas.list_running = listar_e_chegar_o_evento
            assert await reenvio.retry_stalled(agora) == 0

        db.expire_all()
        saga = await SqlAlchemySagaRepository(db).get_by_order(ordem["id"])
        assert saga.current_step == SagaStep.GERAR_ORCAMENTO
        assert saga.steps[SagaStep.RESERVAR_PECAS].status.value == "CONCLUIDA"
        assert [tipo for tipo, _ in await _comandos(db)] == ["ReservarPecas", "GerarOrcamento"]
        gerado = _evento("OrcamentoGerado", saga.id, ordem["id"], CAMINHO[1][1], "billing-service")
        assert (await build_orchestrator(db, notify=False).handle(gerado)).outcome == APPLIED

    async def test_sem_etapas_paradas_nao_faz_nada(self, auth_client, db):
        await _abrir_os(auth_client)
        assert await build_orchestrator(db, notify=False).retry_stalled() == 0


class TestNotificacoes:
    async def test_cliente_e_avisado_de_cada_mudanca(self, auth_client, db):
        ordem = await _abrir_os(auth_client)
        orquestrador = build_orchestrator(db)
        notificador = NotificadorFalso()
        orquestrador._notifier = notificador
        saga_id = await _saga_id(db, ordem["id"])

        for tipo, payload in CAMINHO[:2]:
            await orquestrador.handle(_evento(tipo, saga_id, ordem["id"], payload))

        assert notificador.enviadas == [("maria@exemplo.com", ordem["number"], "AGUARDANDO_APROVACAO")]
