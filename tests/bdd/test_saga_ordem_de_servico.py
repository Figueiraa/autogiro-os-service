"""Passos da funcionalidade "Saga da ordem de serviço".

A OS é aberta, consultada e entregue pela API real do OS Service. Execution e
Billing são simulados respondendo com os eventos do contrato de mensagens, e cada
comando que o orquestrador envia é conferido no outbox.
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from pytest_bdd import given, parsers, scenarios, then, when
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.application.messages import Envelope
from app.application.ports.part_catalog import PartInfo
from app.infrastructure.composition import build_orchestrator
from app.infrastructure.database import Base, get_db
from app.infrastructure.persistence.models.saga_model import OutboxMessage
from app.infrastructure.persistence.repositories.saga_repository import SqlAlchemySagaRepository
from app.infrastructure.security.jwt_tokens import issue_token
from app.interfaces.http.controllers.service_order_controller import get_part_catalog
from app.main import create_app

scenarios("features/saga_ordem_de_servico.feature")


def _reais(valor: str) -> float:
    return float(valor.replace(".", "").replace(",", "."))


class CatalogoDePecas:
    async def find(self, part_id: int) -> PartInfo | None:
        return PartInfo(id=part_id, name=f"Peça {part_id}", unit_price=32.5)


class Oficina:
    """Ambiente de um cenário: API do OS Service, banco e participantes simulados."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.ids: dict[str, int] = {}
        self.ordem: dict = {}
        self.run(self._montar())

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _montar(self):
        self.engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async def sessao():
            async with self.sessions() as db:
                yield db

        app = create_app()
        app.state.readiness_checks = {}
        app.dependency_overrides[get_db] = sessao
        app.dependency_overrides[get_part_catalog] = CatalogoDePecas
        token = issue_token("44232322191", client_id=1, name="Maria")
        self.api = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        )

    def fechar(self):
        self.run(self.api.aclose())
        self.run(self.engine.dispose())
        self.loop.close()

    # ─── Ações ──────────────────────────────────────────────────────────────
    def post(self, rota, corpo=None) -> dict:
        resposta = self.run(self.api.post(rota, json=corpo))
        assert resposta.status_code in (200, 201), resposta.text
        return resposta.json()

    def get(self, rota) -> dict | list:
        return self.run(self.api.get(rota)).json()

    def os_atual(self) -> dict:
        return self.get(f"/api/v1/service-orders/{self.ordem['id']}")

    def saga(self) -> dict:
        return self.get(f"/api/v1/service-orders/{self.ordem['id']}/saga")

    def responder(self, tipo: str, payload: dict | None = None, produtor: str = "execution-service"):
        async def _responder():
            async with self.sessions() as db:
                saga = await SqlAlchemySagaRepository(db).get_by_order(self.ordem["id"])
                evento = Envelope(
                    type=tipo,
                    kind="event",
                    saga_id=saga.id,
                    order_id=self.ordem["id"],
                    payload=payload or {},
                    producer=produtor,
                )
                resultado = await build_orchestrator(db, notify=False).handle(evento)
                assert resultado.outcome == "applied", (tipo, resultado.note)

        self.run(_responder())

    def comandos(self) -> list[tuple[str, dict]]:
        async def _ler():
            async with self.sessions() as db:
                linhas = (await db.execute(select(OutboxMessage).order_by(OutboxMessage.id))).scalars().all()
            return [(linha.message_type, json.loads(linha.body)["payload"]) for linha in linhas]

        return self.run(_ler())


@pytest.fixture
def oficina():
    ambiente = Oficina()
    yield ambiente
    ambiente.fechar()


# ─── Contexto ───────────────────────────────────────────────────────────────


@given(parsers.parse('a cliente "{nome}" com um veículo cadastrado'))
def cliente_com_veiculo(oficina, nome):
    cliente = oficina.post(
        "/api/v1/clients", {"name": nome, "cpf_cnpj": "529.982.247-25", "email": f"{nome.lower()}@exemplo.com"}
    )
    veiculo = oficina.post(
        "/api/v1/vehicles",
        {"client_id": cliente["id"], "plate": "ABC1D23", "brand": "Fiat", "model": "Argo", "year": 2021},
    )
    oficina.ids["veiculo"] = veiculo["id"]


@given(parsers.parse('o serviço "{nome}" de R$ {preco} no catálogo'))
def servico_no_catalogo(oficina, nome, preco):
    oficina.ids["servico"] = oficina.post("/api/v1/service-types", {"name": nome, "price": _reais(preco)})["id"]


# ─── Abertura e reserva ─────────────────────────────────────────────────────


@when(parsers.parse("o atendente abre uma OS com o serviço e {quantidade:d} unidades da peça {peca:d}"))
def abrir_os(oficina, quantidade, peca):
    oficina.ordem = oficina.post(
        "/api/v1/service-orders",
        {
            "vehicle_id": oficina.ids["veiculo"],
            "items": [{"service_type_id": oficina.ids["servico"], "quantity": 1}],
            "parts": [{"part_id": peca, "quantity": quantidade}],
        },
    )
    oficina.ids["peca"], oficina.ids["quantidade"] = peca, quantidade


@then(parsers.parse('o Execution recebe o comando "{comando}"'))
def execution_recebe(oficina, comando):
    tipo, _ = oficina.comandos()[-1]
    assert tipo == comando


@when(parsers.parse("o Execution confirma a reserva da peça {peca:d} a R$ {preco}"))
def reserva_confirmada(oficina, peca, preco):
    item = {"part_id": peca, "name": "Filtro de óleo", "quantity": oficina.ids["quantidade"], "unit_price": _reais(preco)}
    oficina.responder("PecasReservadas", {"items": [item]})


@when(parsers.parse("o Execution responde que a peça {peca:d} está sem estoque"))
def sem_estoque(oficina, peca):
    falta = {"part_id": peca, "requested": oficina.ids["quantidade"], "available": 0, "reason": "SEM_ESTOQUE"}
    oficina.responder("PecasIndisponiveis", {"missing": [falta]})


# ─── Orçamento ──────────────────────────────────────────────────────────────


@then(parsers.parse('o Billing recebe o comando "{comando}" com o total de R$ {total}'))
def billing_recebe(oficina, comando, total):
    tipo, payload = oficina.comandos()[-1]
    soma = sum(s["quantity"] * s["unit_price"] for s in payload["services"]) + sum(
        p["quantity"] * p["unit_price"] for p in payload["parts"]
    )
    assert tipo == comando
    assert round(soma, 2) == _reais(total)


@when(parsers.parse("o Billing gera o orçamento de R$ {total}"))
def orcamento_gerado(oficina, total):
    oficina.responder("OrcamentoGerado", {"budget_id": 1, "total": _reais(total)}, "billing-service")


@then(parsers.parse('a OS fica "{status}" com orçamento de R$ {total}'))
def os_com_orcamento(oficina, status, total):
    ordem = oficina.os_atual()
    assert (ordem["status"], ordem["budget_total"]) == (status, _reais(total))


@when("a cliente aprova o orçamento")
def aprova(oficina):
    oficina.responder(
        "OrcamentoAprovado",
        {"budget_id": 1, "payment_id": 1, "payment_link": "https://mercadopago.test/pagar/1", "amount": 214.9},
        "billing-service",
    )


@when("a cliente recusa o orçamento")
def recusa(oficina):
    oficina.responder("OrcamentoRecusado", {"budget_id": 1}, "billing-service")


@then(parsers.parse('a OS fica "{status}" com o link de pagamento'))
def os_com_link(oficina, status):
    ordem = oficina.os_atual()
    assert (ordem["status"], ordem["payment_link"]) == (status, "https://mercadopago.test/pagar/1")


# ─── Pagamento e execução ───────────────────────────────────────────────────


@when("o Mercado Pago confirma o pagamento")
def pagamento_confirmado(oficina):
    oficina.responder("PagamentoConfirmado", {"payment_id": 1, "mp_payment_id": "123", "amount": 214.9}, "billing-service")


@when("o Mercado Pago recusa o pagamento")
def pagamento_recusado(oficina):
    oficina.responder(
        "PagamentoRecusado",
        {"payment_id": 1, "mp_payment_id": "124", "status_detail": "cc_rejected_other_reason"},
        "billing-service",
    )


@when("a OS entra na fila da oficina")
def na_fila(oficina):
    oficina.responder("ExecucaoEnfileirada", {"position": 1})


@when("o Execution recusa a OS porque a fila está cheia")
def fila_cheia(oficina):
    oficina.responder("EnfileiramentoRecusado", {"reason": "FILA_CHEIA"})


@when("o mecânico faz o diagnóstico, o reparo e finaliza")
def mecanico(oficina):
    for evento in ("DiagnosticoIniciado", "ReparoIniciado", "ExecucaoFinalizada"):
        oficina.responder(evento, {"note": None})


@when("o atendente entrega o veículo")
def entrega(oficina):
    oficina.post(f"/api/v1/service-orders/{oficina.ordem['id']}/deliver")


# ─── Estados prontos para os cenários de falha ──────────────────────────────


def _ate(oficina, eventos):
    abrir_os(oficina, 2, 3)
    reserva_confirmada(oficina, 3, "32,50")
    orcamento_gerado(oficina, "214,90")
    for passo in eventos:
        passo(oficina)


@given("uma OS aguardando a aprovação do orçamento")
def os_aguardando_aprovacao(oficina):
    _ate(oficina, [])


@given("uma OS aguardando o pagamento")
def os_aguardando_pagamento(oficina):
    _ate(oficina, [aprova])


@given("uma OS paga aguardando a fila da oficina")
def os_paga(oficina):
    _ate(oficina, [aprova, pagamento_confirmado])


# ─── Compensações ───────────────────────────────────────────────────────────


@then(parsers.parse('são enviadas as compensações "{primeira}" e "{segunda}"'))
def compensacoes(oficina, primeira, segunda):
    assert [tipo for tipo, _ in oficina.comandos()[-2:]] == [primeira, segunda]


@then("nenhum comando de compensação é enviado")
def sem_compensacao(oficina):
    tipos = [tipo for tipo, _ in oficina.comandos()]
    assert "LiberarPecas" not in tipos and "CancelarCobranca" not in tipos


@when("o Execution confirma a liberação das peças")
def pecas_liberadas(oficina):
    oficina.responder("PecasLiberadas", {"released": True})


@when("o Billing confirma o cancelamento da cobrança")
def cobranca_cancelada(oficina):
    oficina.responder("CobrancaCancelada", {"estornado": False, "refund_id": None}, "billing-service")


@when("o Billing confirma o cancelamento da cobrança com estorno")
def cobranca_estornada(oficina):
    oficina.responder("CobrancaCancelada", {"estornado": True, "refund_id": "999"}, "billing-service")


# ─── Verificações ───────────────────────────────────────────────────────────


@then(parsers.parse('a OS fica "{status}"'))
def os_fica(oficina, status):
    assert oficina.os_atual()["status"] == status


@then(parsers.parse('a OS fica "{status}" pelo motivo "{motivo}"'))
def os_fica_pelo_motivo(oficina, status, motivo):
    ordem = oficina.os_atual()
    assert (ordem["status"], ordem["cancel_reason"]) == (status, motivo)


@then(parsers.parse('a Saga termina "{estado}"'))
def saga_termina(oficina, estado):
    assert oficina.saga()["state"] == estado


@then("o histórico registra cada etapa com o evento que a causou")
def historico(oficina):
    historico = oficina.get(f"/api/v1/service-orders/{oficina.ordem['id']}/history")
    assert [(h["to_status"], h["source"]) for h in historico] == [
        ("RECEBIDA", "api"),
        ("AGUARDANDO_APROVACAO", "OrcamentoGerado"),
        ("AGUARDANDO_PAGAMENTO", "OrcamentoAprovado"),
        ("PAGA", "PagamentoConfirmado"),
        ("NA_FILA", "ExecucaoEnfileirada"),
        ("EM_DIAGNOSTICO", "DiagnosticoIniciado"),
        ("EM_EXECUCAO", "ReparoIniciado"),
        ("FINALIZADA", "ExecucaoFinalizada"),
        ("ENTREGUE", "api"),
    ]
