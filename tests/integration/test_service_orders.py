"""Ordens de serviço pela API: abertura, consulta, histórico e entrega."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.value_objects.service_order_status import ServiceOrderStatus
from app.infrastructure.database import database_is_up
from app.infrastructure.persistence.repositories.service_order_repository import (
    SqlAlchemyServiceOrderRepository,
)

S = ServiceOrderStatus


async def _cadastros(auth_client) -> dict:
    cliente = await auth_client.post(
        "/api/v1/clients",
        json={"name": "Maria", "cpf_cnpj": "529.982.247-25", "email": "maria@exemplo.com"},
    )
    veiculo = await auth_client.post(
        "/api/v1/vehicles",
        json={"client_id": cliente.json()["id"], "plate": "ABC1D23", "brand": "Fiat", "model": "Argo", "year": 2021},
    )
    oleo = await auth_client.post(
        "/api/v1/service-types",
        json={"name": "Troca de óleo", "price": 149.9, "estimated_duration_minutes": 45},
    )
    freio = await auth_client.post(
        "/api/v1/service-types",
        json={"name": "Troca de pastilhas", "price": 320.0, "estimated_duration_minutes": 90},
    )
    return {
        "client_id": cliente.json()["id"],
        "vehicle_id": veiculo.json()["id"],
        "oleo": oleo.json()["id"],
        "freio": freio.json()["id"],
    }


async def _abrir(auth_client, ids, **extra) -> dict:
    payload = {
        "vehicle_id": ids["vehicle_id"],
        "notes": "barulho no freio",
        "items": [{"service_type_id": ids["oleo"], "quantity": 1}, {"service_type_id": ids["freio"]}],
        "parts": [{"part_id": 3, "quantity": 2}],
    }
    payload.update(extra)
    r = await auth_client.post("/api/v1/service-orders", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _avancar(db, order_id: int, ate: ServiceOrderStatus, inicio: datetime | None = None) -> None:
    """Leva a OS até um status, como a Saga fará por eventos."""
    caminho = [S.AGUARDANDO_APROVACAO, S.AGUARDANDO_PAGAMENTO, S.PAGA, S.NA_FILA, S.EM_DIAGNOSTICO,
               S.EM_EXECUCAO, S.FINALIZADA]
    repo = SqlAlchemyServiceOrderRepository(db)
    ordem = await repo.get_by_id(order_id)
    # No passado: a próxima mudança feita pela API (horário real) fica depois no histórico.
    momento = inicio or datetime.now(UTC) - timedelta(hours=3)
    for passo, status in enumerate(caminho[: caminho.index(ate) + 1], start=1):
        ordem.transition_to(status, source="teste", now=momento + timedelta(minutes=10 * passo))
    await repo.update(ordem)


class TestExclusaoDeCadastrosEmUso:
    """O banco recusa a exclusão (RESTRICT): a API responde 409 com o motivo, não 500."""

    async def test_cliente_com_veiculo_responde_409(self, auth_client):
        ids = await _cadastros(auth_client)

        r = await auth_client.delete(f"/api/v1/clients/{ids['client_id']}")

        assert r.status_code == 409
        assert "vinculados" in r.json()["detail"]
        assert (await auth_client.get(f"/api/v1/clients/{ids['client_id']}")).status_code == 200

    async def test_veiculo_com_ordem_de_servico_responde_409(self, auth_client):
        ids = await _cadastros(auth_client)
        await _abrir(auth_client, ids)

        r = await auth_client.delete(f"/api/v1/vehicles/{ids['vehicle_id']}")

        assert r.status_code == 409
        assert "ordens de serviço vinculadas" in r.json()["detail"]

    async def test_servico_usado_em_ordem_de_servico_responde_409(self, auth_client):
        ids = await _cadastros(auth_client)
        await _abrir(auth_client, ids)

        r = await auth_client.delete(f"/api/v1/service-types/{ids['oleo']}")

        assert r.status_code == 409
        assert "já foi usado em ordens de serviço" in r.json()["detail"]

    async def test_servico_sem_uso_continua_podendo_ser_excluido(self, auth_client):
        ids = await _cadastros(auth_client)
        await _abrir(auth_client, ids, items=[{"service_type_id": ids["oleo"]}])

        assert (await auth_client.delete(f"/api/v1/service-types/{ids['freio']}")).status_code == 204


class TestAbertura:
    async def test_abre_a_os_recebida_sem_orcamento(self, auth_client):
        ids = await _cadastros(auth_client)

        ordem = await _abrir(auth_client, ids)

        assert ordem["status"] == "RECEBIDA"
        assert ordem["number"].startswith("OS")
        assert ordem["client_id"] == ids["client_id"]
        assert ordem["services_total"] == 469.9
        # Orçamento e link de pagamento chegam depois, pela Saga.
        assert ordem["budget_total"] is None
        assert ordem["payment_link"] is None
        assert [(i["name"], i["unit_price"], i["subtotal"]) for i in ordem["items"]] == [
            ("Troca de óleo", 149.9, 149.9),
            ("Troca de pastilhas", 320.0, 320.0),
        ]
        # A peça fica só como pedido: nome e preço vêm da reserva no Execution Service.
        assert ordem["parts"] == [
            {"id": ordem["parts"][0]["id"], "part_id": 3, "quantity": 2, "name": None, "unit_price": None}
        ]

    async def test_consulta_o_catalogo_de_pecas_na_abertura(self, auth_client, part_catalog):
        ids = await _cadastros(auth_client)
        await _abrir(auth_client, ids, parts=[{"part_id": 3}, {"part_id": 4}])
        assert part_catalog.lookups == [3, 4]

    async def test_peca_inexistente_no_catalogo_responde_422(self, auth_client):
        ids = await _cadastros(auth_client)
        r = await auth_client.post(
            "/api/v1/service-orders",
            json={"vehicle_id": ids["vehicle_id"], "items": [{"service_type_id": ids["oleo"]}], "parts": [{"part_id": 99}]},
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "Peça 99 não existe no catálogo"

    async def test_catalogo_fora_do_ar_nao_impede_a_abertura(self, auth_client, part_catalog):
        part_catalog.available = False
        ids = await _cadastros(auth_client)

        ordem = await _abrir(auth_client, ids, parts=[{"part_id": 99}])

        assert ordem["status"] == "RECEBIDA"
        assert ordem["parts"][0]["part_id"] == 99

    async def test_exige_pelo_menos_um_servico(self, auth_client):
        ids = await _cadastros(auth_client)
        r = await auth_client.post("/api/v1/service-orders", json={"vehicle_id": ids["vehicle_id"], "items": []})
        assert r.status_code == 422

    async def test_veiculo_inexistente_responde_404(self, auth_client):
        ids = await _cadastros(auth_client)
        r = await auth_client.post(
            "/api/v1/service-orders", json={"vehicle_id": 999, "items": [{"service_type_id": ids["oleo"]}]}
        )
        assert r.status_code == 404

    async def test_servico_inexistente_responde_404(self, auth_client):
        ids = await _cadastros(auth_client)
        r = await auth_client.post(
            "/api/v1/service-orders", json={"vehicle_id": ids["vehicle_id"], "items": [{"service_type_id": 999}]}
        )
        assert r.status_code == 404

    async def test_numeros_sao_sequenciais(self, auth_client):
        ids = await _cadastros(auth_client)
        primeira = await _abrir(auth_client, ids)
        segunda = await _abrir(auth_client, ids)
        assert int(segunda["number"][-4:]) == int(primeira["number"][-4:]) + 1

    async def test_numero_vem_do_id_da_os(self, auth_client):
        # Único por construção: contar as OS existentes repetia o número em aberturas
        # simultâneas (as duas contavam antes de qualquer uma gravar).
        ids = await _cadastros(auth_client)

        ordem = await _abrir(auth_client, ids)

        assert re.fullmatch(r"OS\d{8}\d{4}", ordem["number"])
        assert ordem["number"].endswith(f"{ordem['id']:04d}")

    async def test_exige_autenticacao(self, client):
        r = await client.post("/api/v1/service-orders", json={"vehicle_id": 1, "items": [{"service_type_id": 1}]})
        assert r.status_code == 401


class TestConsulta:
    async def test_detalha_a_os(self, auth_client):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)

        r = await auth_client.get(f"/api/v1/service-orders/{ordem['id']}")

        assert r.status_code == 200
        assert r.json()["number"] == ordem["number"]

    async def test_os_inexistente_responde_404(self, auth_client):
        assert (await auth_client.get("/api/v1/service-orders/999")).status_code == 404

    async def test_historico_registra_cada_mudanca(self, auth_client, db):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)
        await _avancar(db, ordem["id"], S.AGUARDANDO_PAGAMENTO)

        r = await auth_client.get(f"/api/v1/service-orders/{ordem['id']}/history")

        assert r.status_code == 200
        assert [(h["from_status"], h["to_status"], h["source"]) for h in r.json()] == [
            (None, "RECEBIDA", "api"),
            ("RECEBIDA", "AGUARDANDO_APROVACAO", "teste"),
            ("AGUARDANDO_APROVACAO", "AGUARDANDO_PAGAMENTO", "teste"),
        ]

    async def test_status_publico_dispensa_autenticacao(self, auth_client, client):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)

        r = await client.get(f"/api/v1/service-orders/{ordem['id']}/status")

        assert r.status_code == 200
        assert r.json() == {"number": ordem["number"], "status": "RECEBIDA"}


class TestListagem:
    async def test_lista_ativas_por_prioridade_e_esconde_encerradas(self, auth_client, db):
        ids = await _cadastros(auth_client)
        recebida = await _abrir(auth_client, ids)
        em_execucao = await _abrir(auth_client, ids)
        finalizada = await _abrir(auth_client, ids)
        await _avancar(db, em_execucao["id"], S.EM_EXECUCAO)
        await _avancar(db, finalizada["id"], S.FINALIZADA)

        r = await auth_client.get("/api/v1/service-orders")

        assert [o["id"] for o in r.json()] == [em_execucao["id"], recebida["id"]]

    async def test_filtra_por_status(self, auth_client, db):
        ids = await _cadastros(auth_client)
        await _abrir(auth_client, ids)
        finalizada = await _abrir(auth_client, ids)
        await _avancar(db, finalizada["id"], S.FINALIZADA)

        r = await auth_client.get("/api/v1/service-orders", params={"status": "FINALIZADA"})

        assert [o["id"] for o in r.json()] == [finalizada["id"]]


class TestEntrega:
    async def test_entrega_a_os_finalizada(self, auth_client, db):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)
        await _avancar(db, ordem["id"], S.FINALIZADA)

        r = await auth_client.post(f"/api/v1/service-orders/{ordem['id']}/deliver")

        assert r.status_code == 200
        assert r.json()["status"] == "ENTREGUE"
        assert r.json()["delivered_at"] is not None
        historico = (await auth_client.get(f"/api/v1/service-orders/{ordem['id']}/history")).json()
        assert historico[-1]["to_status"] == "ENTREGUE"

    async def test_nao_entrega_os_que_nao_foi_finalizada(self, auth_client):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)

        r = await auth_client.post(f"/api/v1/service-orders/{ordem['id']}/deliver")

        assert r.status_code == 422
        assert "RECEBIDA" in r.json()["detail"]

    async def test_entregar_os_inexistente_responde_404(self, auth_client):
        assert (await auth_client.post("/api/v1/service-orders/999/deliver")).status_code == 404


class TestTempoMedioDeExecucao:
    async def test_sem_os_entregues(self, auth_client):
        r = await auth_client.get("/api/v1/service-orders/metrics/average-execution-time")
        assert r.json() == {"average_minutes": None, "total_completed": 0}

    async def test_media_entre_inicio_do_reparo_e_finalizacao(self, auth_client, db):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)
        await _avancar(db, ordem["id"], S.FINALIZADA)
        await auth_client.post(f"/api/v1/service-orders/{ordem['id']}/deliver")

        r = await auth_client.get("/api/v1/service-orders/metrics/average-execution-time")

        # Em _avancar cada passo dura 10 minutos: do reparo à finalização são 10.
        assert r.json() == {"average_minutes": 10.0, "total_completed": 1}


class TestPersistencia:
    async def test_copias_de_pecas_e_orcamento_sao_gravadas(self, auth_client, db):
        ids = await _cadastros(auth_client)
        ordem = await _abrir(auth_client, ids)
        repo = SqlAlchemyServiceOrderRepository(db)

        entidade = await repo.get_by_id(ordem["id"])
        entidade.parts[0].name, entidade.parts[0].unit_price = "Filtro de óleo", 32.5
        entidade.budget_total = 534.9
        entidade.payment_link = "https://sandbox.mercadopago.com/checkout/123"
        salva = await repo.update(entidade)

        assert (salva.parts[0].name, salva.parts[0].unit_price) == ("Filtro de óleo", 32.5)
        assert (salva.budget_total, salva.payment_link) == (534.9, "https://sandbox.mercadopago.com/checkout/123")


@pytest.mark.parametrize("_", [None])
async def test_verificacao_do_banco_real_responde(_):
    assert await database_is_up() is True
