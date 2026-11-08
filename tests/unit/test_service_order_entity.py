"""Máquina de estados e histórico da Ordem de Serviço."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities.service_order import (
    ServiceOrder,
    ServiceOrderItem,
    ServiceOrderPart,
    _seconds_between,
)
from app.domain.exceptions import BusinessRuleError, InvalidStatusTransitionError
from app.domain.value_objects.service_order_status import (
    HIDDEN_FROM_ACTIVE_LIST,
    LIST_PRIORITY,
    VALID_TRANSITIONS,
    CancelReason,
    ServiceOrderStatus,
)

S = ServiceOrderStatus
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

CAMINHO_FELIZ = [
    S.AGUARDANDO_APROVACAO,
    S.AGUARDANDO_PAGAMENTO,
    S.PAGA,
    S.NA_FILA,
    S.EM_DIAGNOSTICO,
    S.EM_EXECUCAO,
    S.FINALIZADA,
    S.ENTREGUE,
]


def _os(**kwargs) -> ServiceOrder:
    return ServiceOrder.open(
        number="OS202611100001",
        vehicle_id=1,
        client_id=1,
        items=[ServiceOrderItem(service_type_id=1, name="Troca de óleo", quantity=2, unit_price=149.9)],
        parts=[ServiceOrderPart(part_id=3, quantity=1)],
        now=T0,
        **kwargs,
    )


class TestAbertura:
    def test_abre_recebida_com_o_primeiro_registro_do_historico(self):
        ordem = _os(notes="barulho no freio")

        assert ordem.status == S.RECEBIDA
        assert ordem.notes == "barulho no freio"
        assert len(ordem.history) == 1
        registro = ordem.history[0]
        assert (registro.from_status, registro.to_status, registro.source) == (None, S.RECEBIDA, "api")
        assert registro.occurred_at == T0

    def test_exige_pelo_menos_um_servico(self):
        with pytest.raises(BusinessRuleError, match="pelo menos um serviço"):
            ServiceOrder.open(number="X", vehicle_id=1, client_id=1, items=[], parts=[])

    def test_total_dos_servicos(self):
        ordem = _os()
        ordem.items.append(ServiceOrderItem(service_type_id=2, name="Alinhamento", quantity=1, unit_price=89.9))

        assert ordem.items[0].subtotal == 299.8
        assert ordem.services_total == 389.7

    def test_sem_horario_usa_o_relogio(self):
        ordem = ServiceOrder.open(
            number="X",
            vehicle_id=1,
            client_id=1,
            items=[ServiceOrderItem(service_type_id=1, name="A", quantity=1, unit_price=1)],
            parts=[],
        )
        assert ordem.created_at is not None


class TestTransicoes:
    def test_caminho_feliz_ate_a_entrega(self):
        ordem = _os()
        for passo, status in enumerate(CAMINHO_FELIZ, start=1):
            ordem.transition_to(status, source="teste", now=T0 + timedelta(hours=passo))

        assert ordem.status == S.ENTREGUE
        assert [registro.to_status for registro in ordem.history] == [S.RECEBIDA, *CAMINHO_FELIZ]

    def test_marca_inicio_conclusao_e_entrega(self):
        ordem = _os()
        for passo, status in enumerate(CAMINHO_FELIZ, start=1):
            ordem.transition_to(status, now=T0 + timedelta(hours=passo))

        assert ordem.started_at == T0 + timedelta(hours=6)
        assert ordem.completed_at == T0 + timedelta(hours=7)
        assert ordem.delivered_at == T0 + timedelta(hours=8)

    def test_devolve_o_tempo_no_status_anterior(self):
        ordem = _os()

        registro, segundos = ordem.transition_to(S.AGUARDANDO_APROVACAO, now=T0 + timedelta(minutes=30))

        assert segundos == 1800
        assert registro.from_status == S.RECEBIDA

    def test_nao_pula_etapas(self):
        ordem = _os()
        with pytest.raises(InvalidStatusTransitionError, match="RECEBIDA.*ENTREGUE"):
            ordem.transition_to(S.ENTREGUE)
        assert ordem.status == S.RECEBIDA
        assert len(ordem.history) == 1

    def test_finalizada_nao_pode_mais_ser_cancelada(self):
        ordem = _os()
        for status in CAMINHO_FELIZ[:-1]:
            ordem.transition_to(status)

        with pytest.raises(InvalidStatusTransitionError):
            ordem.transition_to(S.EM_CANCELAMENTO, reason=CancelReason.EXECUCAO_CANCELADA.value)


class TestCancelamento:
    @pytest.mark.parametrize("status", [S.RECEBIDA, S.AGUARDANDO_PAGAMENTO, S.NA_FILA, S.EM_EXECUCAO])
    def test_qualquer_etapa_antes_da_finalizacao_pode_ser_cancelada(self, status):
        ordem = _os()
        for passo in CAMINHO_FELIZ[: CAMINHO_FELIZ.index(status) + 1] if status != S.RECEBIDA else []:
            ordem.transition_to(passo)

        ordem.transition_to(S.EM_CANCELAMENTO, source="PecasIndisponiveis", reason="PECAS_INDISPONIVEIS")
        ordem.transition_to(S.CANCELADA, source="saga")

        assert ordem.status == S.CANCELADA
        assert ordem.cancel_reason == "PECAS_INDISPONIVEIS"
        assert ordem.history[-2].reason == "PECAS_INDISPONIVEIS"

    def test_cancelamento_exige_um_motivo_valido(self):
        ordem = _os()
        with pytest.raises(BusinessRuleError, match="Motivo de cancelamento"):
            ordem.transition_to(S.EM_CANCELAMENTO, reason="PORQUE_SIM")
        with pytest.raises(BusinessRuleError):
            ordem.transition_to(S.EM_CANCELAMENTO)

    def test_em_cancelamento_so_vai_para_cancelada(self):
        ordem = _os()
        ordem.transition_to(S.EM_CANCELAMENTO, reason="ORCAMENTO_RECUSADO")
        with pytest.raises(InvalidStatusTransitionError):
            ordem.transition_to(S.AGUARDANDO_APROVACAO)


class TestMaquinaDeEstados:
    def test_todo_status_tem_regra_de_transicao(self):
        assert set(VALID_TRANSITIONS) == set(S)

    def test_estados_finais_nao_tem_saida(self):
        assert VALID_TRANSITIONS[S.ENTREGUE] == []
        assert VALID_TRANSITIONS[S.CANCELADA] == []

    def test_todo_status_e_alcancavel_a_partir_de_recebida(self):
        alcancados, fila = {S.RECEBIDA}, [S.RECEBIDA]
        while fila:
            for proximo in VALID_TRANSITIONS[fila.pop()]:
                if proximo not in alcancados:
                    alcancados.add(proximo)
                    fila.append(proximo)
        assert alcancados == set(S)

    def test_listagem_ativa_esconde_os_encerradas_e_prioriza_o_restante(self):
        assert set(HIDDEN_FROM_ACTIVE_LIST) | set(LIST_PRIORITY) == set(S)
        assert LIST_PRIORITY[S.EM_EXECUCAO] < LIST_PRIORITY[S.AGUARDANDO_APROVACAO]
        assert LIST_PRIORITY[S.EM_DIAGNOSTICO] < LIST_PRIORITY[S.RECEBIDA]


class TestSegundosEntre:
    def test_sem_marco_anterior_devolve_none(self):
        assert _seconds_between(None, T0) is None

    def test_aceita_horarios_sem_fuso(self):
        inicio = datetime(2026, 11, 10, 9, 0)
        fim = datetime(2026, 11, 10, 9, 1)
        assert _seconds_between(inicio, fim) == 60

    def test_nunca_negativo(self):
        assert _seconds_between(T0, T0 - timedelta(seconds=5)) == 0
