"""Decisões da Saga: caminho feliz, matriz de compensações, idempotência e reenvio."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities.saga import Saga, SagaState, SagaStep, StepStatus
from app.domain.exceptions import OutOfOrderEventError
from app.domain.value_objects.service_order_status import ServiceOrderStatus as S

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

# Eventos de sucesso até cada etapa, na ordem.
ATE = {
    SagaStep.GERAR_ORCAMENTO: ["PecasReservadas"],
    SagaStep.AGUARDAR_APROVACAO: ["PecasReservadas", "OrcamentoGerado"],
    SagaStep.AGUARDAR_PAGAMENTO: ["PecasReservadas", "OrcamentoGerado", "OrcamentoAprovado"],
    SagaStep.ENFILEIRAR_EXECUCAO: ["PecasReservadas", "OrcamentoGerado", "OrcamentoAprovado", "PagamentoConfirmado"],
    SagaStep.ACOMPANHAR_EXECUCAO: [
        "PecasReservadas",
        "OrcamentoGerado",
        "OrcamentoAprovado",
        "PagamentoConfirmado",
        "ExecucaoEnfileirada",
    ],
}


def _saga_em(step: SagaStep | None = None) -> Saga:
    saga, _ = Saga.start(order_id=7, now=T0)
    for evento in ATE.get(step, []):
        saga.apply(evento, now=T0)
    return saga


class TestCaminhoFeliz:
    def test_inicio_pede_a_reserva_de_pecas(self):
        saga, decisao = Saga.start(order_id=7, now=T0)

        assert decisao.commands == ["ReservarPecas"]
        assert saga.state == SagaState.EM_ANDAMENTO
        assert saga.current_step == SagaStep.RESERVAR_PECAS
        assert saga.steps[SagaStep.RESERVAR_PECAS].attempts == 1

    def test_cada_evento_leva_ao_proximo_passo(self):
        saga, _ = Saga.start(order_id=7, now=T0)
        roteiro = [
            ("PecasReservadas", ["GerarOrcamento"], []),
            ("OrcamentoGerado", [], [(S.AGUARDANDO_APROVACAO, None)]),
            ("OrcamentoAprovado", [], [(S.AGUARDANDO_PAGAMENTO, None)]),
            ("PagamentoConfirmado", ["EnfileirarExecucao"], [(S.PAGA, None)]),
            ("ExecucaoEnfileirada", [], [(S.NA_FILA, None)]),
            ("DiagnosticoIniciado", [], [(S.EM_DIAGNOSTICO, None)]),
            ("ReparoIniciado", [], [(S.EM_EXECUCAO, None)]),
            ("ExecucaoFinalizada", [], [(S.FINALIZADA, None)]),
        ]
        for evento, comandos, transicoes in roteiro:
            decisao = saga.apply(evento, now=T0)
            assert (decisao.commands, decisao.order_transitions) == (comandos, transicoes), evento

        assert saga.state == SagaState.CONCLUIDA
        assert saga.current_step is None
        assert saga.finished_at == T0
        assert all(r.status == StepStatus.CONCLUIDA for r in saga.steps.values())


class TestMatrizDeCompensacoes:
    @pytest.mark.parametrize(
        ("etapa", "falha", "motivo", "compensacoes"),
        [
            (SagaStep.GERAR_ORCAMENTO, "OrcamentoFalhou", "ORCAMENTO_FALHOU", ["LiberarPecas"]),
            (SagaStep.AGUARDAR_APROVACAO, "OrcamentoRecusado", "ORCAMENTO_RECUSADO", ["LiberarPecas", "CancelarCobranca"]),
            (SagaStep.AGUARDAR_APROVACAO, "OrcamentoExpirado", "APROVACAO_EXPIRADA", ["LiberarPecas", "CancelarCobranca"]),
            (SagaStep.AGUARDAR_PAGAMENTO, "PagamentoRecusado", "PAGAMENTO_RECUSADO", ["LiberarPecas", "CancelarCobranca"]),
            (SagaStep.AGUARDAR_PAGAMENTO, "PagamentoExpirado", "PAGAMENTO_EXPIRADO", ["LiberarPecas", "CancelarCobranca"]),
            (SagaStep.ENFILEIRAR_EXECUCAO, "EnfileiramentoRecusado", "EXECUCAO_RECUSADA", ["LiberarPecas", "CancelarCobranca"]),
            (SagaStep.ACOMPANHAR_EXECUCAO, "ExecucaoCancelada", "EXECUCAO_CANCELADA", ["LiberarPecas", "CancelarCobranca"]),
        ],
    )
    def test_falha_dispara_so_as_compensacoes_do_que_foi_feito(self, etapa, falha, motivo, compensacoes):
        saga = _saga_em(etapa)

        decisao = saga.apply(falha, now=T0)

        assert decisao.commands == compensacoes
        assert decisao.order_transitions == [(S.EM_CANCELAMENTO, motivo)]
        assert saga.state == SagaState.COMPENSANDO
        assert saga.failure_reason == motivo
        assert saga.steps[etapa].status == StepStatus.FALHOU

    def test_pecas_indisponiveis_cancela_sem_compensar_nada(self):
        saga = _saga_em()

        decisao = saga.apply("PecasIndisponiveis", now=T0)

        assert decisao.commands == []
        assert decisao.order_transitions == [(S.EM_CANCELAMENTO, "PECAS_INDISPONIVEIS"), (S.CANCELADA, None)]
        assert saga.state == SagaState.COMPENSADA

    def test_os_so_e_cancelada_quando_todas_as_compensacoes_confirmam(self):
        saga = _saga_em(SagaStep.ENFILEIRAR_EXECUCAO)
        saga.apply("EnfileiramentoRecusado", now=T0)

        primeira = saga.apply("PecasLiberadas", now=T0)
        assert (primeira.order_transitions, saga.state) == ([], SagaState.COMPENSANDO)

        segunda = saga.apply("CobrancaCancelada", now=T0)
        assert segunda.order_transitions == [(S.CANCELADA, None)]
        assert saga.state == SagaState.COMPENSADA
        assert saga.finished_at == T0


class TestIdempotenciaEOrdem:
    def test_evento_repetido_e_ignorado(self):
        saga = _saga_em(SagaStep.GERAR_ORCAMENTO)
        assert saga.apply("PecasReservadas", now=T0).ignored

    def test_evento_de_etapa_ja_concluida_e_ignorado(self):
        saga = _saga_em(SagaStep.AGUARDAR_PAGAMENTO)

        decisao = saga.apply("OrcamentoGerado", now=T0)

        assert decisao.ignored
        assert saga.current_step == SagaStep.AGUARDAR_PAGAMENTO

    @pytest.mark.parametrize(
        ("etapa", "evento"),
        [
            (SagaStep.RESERVAR_PECAS, "PagamentoConfirmado"),
            (SagaStep.GERAR_ORCAMENTO, "OrcamentoAprovado"),
            (SagaStep.AGUARDAR_APROVACAO, "PagamentoConfirmado"),
            (SagaStep.AGUARDAR_PAGAMENTO, "ExecucaoEnfileirada"),
            (SagaStep.ENFILEIRAR_EXECUCAO, "DiagnosticoIniciado"),
        ],
    )
    def test_evento_de_etapa_futura_pede_nova_tentativa_sem_mudar_nada(self, etapa, evento):
        # Consumo em paralelo: o evento chegou antes do que o precede e precisa voltar depois.
        saga = _saga_em(etapa)
        antes = (saga.state, saga.current_step, saga.updated_at, {k: v.status for k, v in saga.steps.items()})

        with pytest.raises(OutOfOrderEventError):
            saga.apply(evento, now=T0 + timedelta(minutes=1))

        assert (saga.state, saga.current_step, saga.updated_at, {k: v.status for k, v in saga.steps.items()}) == antes

    def test_evento_desconhecido_e_ignorado(self):
        assert _saga_em().apply("AlgoQueNaoExiste", now=T0).ignored

    def test_saga_encerrada_ignora_eventos(self):
        saga = _saga_em(SagaStep.ACOMPANHAR_EXECUCAO)
        saga.apply("ExecucaoFinalizada", now=T0)
        assert saga.apply("ExecucaoCancelada", now=T0).ignored

    def test_confirmacao_de_compensacao_repetida_e_ignorada(self):
        saga = _saga_em(SagaStep.GERAR_ORCAMENTO)
        saga.apply("OrcamentoFalhou", now=T0)
        saga.apply("PecasLiberadas", now=T0)

        assert saga.apply("PecasLiberadas", now=T0).ignored

    def test_confirmacao_sem_compensacao_pendente_e_ignorada(self):
        assert _saga_em(SagaStep.GERAR_ORCAMENTO).apply("CobrancaCancelada", now=T0).ignored


class TestAndamentoDaExecucao:
    """Diagnóstico, reparo e finalização são da mesma etapa: a ordem vem do status da OS."""

    @pytest.mark.parametrize(
        ("status_da_os", "evento"),
        [
            (S.NA_FILA, "ReparoIniciado"),
            (S.NA_FILA, "ExecucaoFinalizada"),
            (S.EM_DIAGNOSTICO, "ExecucaoFinalizada"),
        ],
    )
    def test_evento_que_pula_status_pede_nova_tentativa(self, status_da_os, evento):
        saga = _saga_em(SagaStep.ACOMPANHAR_EXECUCAO)

        with pytest.raises(OutOfOrderEventError):
            saga.apply(evento, now=T0, order_status=status_da_os)

        # A Saga não termina antes de a OS passar pelo reparo.
        assert (saga.state, saga.current_step) == (SagaState.EM_ANDAMENTO, SagaStep.ACOMPANHAR_EXECUCAO)

    @pytest.mark.parametrize(
        ("status_da_os", "evento"),
        [
            (S.EM_DIAGNOSTICO, "DiagnosticoIniciado"),
            (S.EM_EXECUCAO, "DiagnosticoIniciado"),
            (S.EM_EXECUCAO, "ReparoIniciado"),
        ],
    )
    def test_evento_de_status_ja_alcancado_e_ignorado(self, status_da_os, evento):
        saga = _saga_em(SagaStep.ACOMPANHAR_EXECUCAO)

        decisao = saga.apply(evento, now=T0, order_status=status_da_os)

        assert decisao.ignored
        assert decisao.order_transitions == []

    @pytest.mark.parametrize(
        ("status_da_os", "evento", "transicao"),
        [
            (S.NA_FILA, "DiagnosticoIniciado", S.EM_DIAGNOSTICO),
            (S.EM_DIAGNOSTICO, "ReparoIniciado", S.EM_EXECUCAO),
            (S.EM_EXECUCAO, "ExecucaoFinalizada", S.FINALIZADA),
        ],
    )
    def test_proximo_status_e_aplicado(self, status_da_os, evento, transicao):
        saga = _saga_em(SagaStep.ACOMPANHAR_EXECUCAO)

        decisao = saga.apply(evento, now=T0, order_status=status_da_os)

        assert decisao.order_transitions == [(transicao, None)]

    def test_cancelamento_vale_em_qualquer_ponto_da_execucao(self):
        saga = _saga_em(SagaStep.ACOMPANHAR_EXECUCAO)

        decisao = saga.apply("ExecucaoCancelada", now=T0, order_status=S.EM_DIAGNOSTICO)

        assert decisao.order_transitions == [(S.EM_CANCELAMENTO, "EXECUCAO_CANCELADA")]
        assert saga.state == SagaState.COMPENSANDO


class TestPagamentoTardio:
    def test_durante_a_compensacao_pede_o_estorno_de_novo(self):
        saga = _saga_em(SagaStep.AGUARDAR_PAGAMENTO)
        saga.apply("PagamentoExpirado", now=T0)
        saga.apply("CobrancaCancelada", now=T0)  # cancelou antes de o pagamento chegar

        decisao = saga.apply("PagamentoConfirmado", now=T0)

        assert decisao.commands == ["CancelarCobranca"]
        assert saga.steps[SagaStep.CANCELAR_COBRANCA].status == StepStatus.PENDENTE
        assert saga.state == SagaState.COMPENSANDO

    def test_com_a_saga_ja_compensada_estorna_sem_reabrir(self):
        saga = _saga_em(SagaStep.AGUARDAR_PAGAMENTO)
        saga.apply("PagamentoExpirado", now=T0)
        saga.apply("PecasLiberadas", now=T0)
        saga.apply("CobrancaCancelada", now=T0)

        decisao = saga.apply("PagamentoConfirmado", now=T0)

        assert decisao.commands == ["CancelarCobranca"]
        assert decisao.order_transitions == []
        assert saga.state == SagaState.COMPENSADA


class TestReenvioDeEtapasParadas:
    def test_so_reenvia_comando_sem_resposta_ha_mais_que_o_limite(self):
        saga = _saga_em()
        limite = timedelta(seconds=120)

        assert saga.stalled_steps(T0 + timedelta(seconds=119), limite) == []
        paradas = saga.stalled_steps(T0 + timedelta(seconds=120), limite)
        assert [r.step for r in paradas] == [SagaStep.RESERVAR_PECAS]

    def test_reenvio_conta_tentativas(self):
        saga = _saga_em()
        registro = saga.steps[SagaStep.RESERVAR_PECAS]

        comando = saga.mark_resent(registro, T0 + timedelta(minutes=3))

        assert comando == "ReservarPecas"
        assert registro.attempts == 2
        assert registro.last_sent_at == T0 + timedelta(minutes=3)

    def test_etapas_de_espera_nao_sao_reenviadas(self):
        saga = _saga_em(SagaStep.AGUARDAR_APROVACAO)
        assert saga.stalled_steps(T0 + timedelta(days=1), timedelta(seconds=1)) == []

    def test_aceita_horario_sem_fuso_vindo_do_banco(self):
        saga = _saga_em()
        saga.steps[SagaStep.RESERVAR_PECAS].last_sent_at = T0.replace(tzinfo=None)
        assert saga.stalled_steps(T0 + timedelta(minutes=5), timedelta(seconds=120))
