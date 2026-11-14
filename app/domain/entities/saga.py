"""Saga orquestrada da ordem de serviço.

Decisão pura: recebe um evento de um participante e devolve o que fazer em seguida
(comandos a enviar e status a aplicar na OS). Não acessa banco nem rede: quem
executa a decisão é o orquestrador, na camada de aplicação.

Etapas, na ordem:
    RESERVAR_PECAS (Execution) → GERAR_ORCAMENTO (Billing) → AGUARDAR_APROVACAO
    → AGUARDAR_PAGAMENTO → ENFILEIRAR_EXECUCAO (Execution) → ACOMPANHAR_EXECUCAO

Compensações, enviadas em paralelo quando qualquer etapa falha:
    LIBERAR_PECAS     — se as peças chegaram a ser reservadas
    CANCELAR_COBRANCA — se o orçamento chegou a ser gerado (estorna se já foi pago)

Os eventos são consumidos em paralelo (réplicas, prefetch, fila de retry), então
um evento pode chegar antes do que o precede. Evento de etapa já passada é
repetição e é ignorado; evento de etapa futura levanta `OutOfOrderEventError` e
volta mais tarde pela fila de retry.
"""

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.exceptions import OutOfOrderEventError
from app.domain.value_objects.service_order_status import CancelReason, ServiceOrderStatus


class SagaState(str, enum.Enum):
    EM_ANDAMENTO = "EM_ANDAMENTO"
    COMPENSANDO = "COMPENSANDO"
    CONCLUIDA = "CONCLUIDA"
    COMPENSADA = "COMPENSADA"


class SagaStep(str, enum.Enum):
    RESERVAR_PECAS = "RESERVAR_PECAS"
    GERAR_ORCAMENTO = "GERAR_ORCAMENTO"
    AGUARDAR_APROVACAO = "AGUARDAR_APROVACAO"
    AGUARDAR_PAGAMENTO = "AGUARDAR_PAGAMENTO"
    ENFILEIRAR_EXECUCAO = "ENFILEIRAR_EXECUCAO"
    ACOMPANHAR_EXECUCAO = "ACOMPANHAR_EXECUCAO"
    LIBERAR_PECAS = "LIBERAR_PECAS"
    CANCELAR_COBRANCA = "CANCELAR_COBRANCA"


class StepStatus(str, enum.Enum):
    PENDENTE = "PENDENTE"
    CONCLUIDA = "CONCLUIDA"
    FALHOU = "FALHOU"


# Ordem das etapas do caminho principal (as compensações ficam fora).
_STEP_ORDER: list[SagaStep] = [
    SagaStep.RESERVAR_PECAS,
    SagaStep.GERAR_ORCAMENTO,
    SagaStep.AGUARDAR_APROVACAO,
    SagaStep.AGUARDAR_PAGAMENTO,
    SagaStep.ENFILEIRAR_EXECUCAO,
    SagaStep.ACOMPANHAR_EXECUCAO,
]

# Comando enviado ao iniciar cada etapa (etapas de espera não têm comando).
STEP_COMMAND: dict[SagaStep, str] = {
    SagaStep.RESERVAR_PECAS: "ReservarPecas",
    SagaStep.GERAR_ORCAMENTO: "GerarOrcamento",
    SagaStep.ENFILEIRAR_EXECUCAO: "EnfileirarExecucao",
    SagaStep.LIBERAR_PECAS: "LiberarPecas",
    SagaStep.CANCELAR_COBRANCA: "CancelarCobranca",
}

# Etapa que cada evento responde.
EVENT_STEP: dict[str, SagaStep] = {
    "PecasReservadas": SagaStep.RESERVAR_PECAS,
    "PecasIndisponiveis": SagaStep.RESERVAR_PECAS,
    "OrcamentoGerado": SagaStep.GERAR_ORCAMENTO,
    "OrcamentoFalhou": SagaStep.GERAR_ORCAMENTO,
    "OrcamentoAprovado": SagaStep.AGUARDAR_APROVACAO,
    "OrcamentoRecusado": SagaStep.AGUARDAR_APROVACAO,
    "OrcamentoExpirado": SagaStep.AGUARDAR_APROVACAO,
    "PagamentoConfirmado": SagaStep.AGUARDAR_PAGAMENTO,
    "PagamentoRecusado": SagaStep.AGUARDAR_PAGAMENTO,
    "PagamentoExpirado": SagaStep.AGUARDAR_PAGAMENTO,
    "ExecucaoEnfileirada": SagaStep.ENFILEIRAR_EXECUCAO,
    "EnfileiramentoRecusado": SagaStep.ENFILEIRAR_EXECUCAO,
    "DiagnosticoIniciado": SagaStep.ACOMPANHAR_EXECUCAO,
    "ReparoIniciado": SagaStep.ACOMPANHAR_EXECUCAO,
    "ExecucaoFinalizada": SagaStep.ACOMPANHAR_EXECUCAO,
    "ExecucaoCancelada": SagaStep.ACOMPANHAR_EXECUCAO,
}

# Evento de sucesso → (próxima etapa, status da OS). `None` na etapa = a Saga termina.
_ADVANCE: dict[str, tuple[SagaStep | None, ServiceOrderStatus | None]] = {
    "PecasReservadas": (SagaStep.GERAR_ORCAMENTO, None),
    "OrcamentoGerado": (SagaStep.AGUARDAR_APROVACAO, ServiceOrderStatus.AGUARDANDO_APROVACAO),
    "OrcamentoAprovado": (SagaStep.AGUARDAR_PAGAMENTO, ServiceOrderStatus.AGUARDANDO_PAGAMENTO),
    "PagamentoConfirmado": (SagaStep.ENFILEIRAR_EXECUCAO, ServiceOrderStatus.PAGA),
    "ExecucaoEnfileirada": (SagaStep.ACOMPANHAR_EXECUCAO, ServiceOrderStatus.NA_FILA),
    "ExecucaoFinalizada": (None, ServiceOrderStatus.FINALIZADA),
}

# Andamento da execução: muda o status da OS sem mudar de etapa.
_PROGRESS: dict[str, ServiceOrderStatus] = {
    "DiagnosticoIniciado": ServiceOrderStatus.EM_DIAGNOSTICO,
    "ReparoIniciado": ServiceOrderStatus.EM_EXECUCAO,
}

# Status da OS durante a execução, na ordem em que acontecem. Os três eventos de
# andamento são da mesma etapa: a ordem entre eles vem do status da OS.
_EXECUTION_ORDER: list[ServiceOrderStatus] = [
    ServiceOrderStatus.NA_FILA,
    ServiceOrderStatus.EM_DIAGNOSTICO,
    ServiceOrderStatus.EM_EXECUCAO,
    ServiceOrderStatus.FINALIZADA,
]
_EXECUTION_TARGET: dict[str, ServiceOrderStatus] = {
    "DiagnosticoIniciado": ServiceOrderStatus.EM_DIAGNOSTICO,
    "ReparoIniciado": ServiceOrderStatus.EM_EXECUCAO,
    "ExecucaoFinalizada": ServiceOrderStatus.FINALIZADA,
}

# Evento de falha → motivo do cancelamento.
_FAILURE: dict[str, CancelReason] = {
    "PecasIndisponiveis": CancelReason.PECAS_INDISPONIVEIS,
    "OrcamentoFalhou": CancelReason.ORCAMENTO_FALHOU,
    "OrcamentoRecusado": CancelReason.ORCAMENTO_RECUSADO,
    "OrcamentoExpirado": CancelReason.APROVACAO_EXPIRADA,
    "PagamentoRecusado": CancelReason.PAGAMENTO_RECUSADO,
    "PagamentoExpirado": CancelReason.PAGAMENTO_EXPIRADO,
    "EnfileiramentoRecusado": CancelReason.EXECUCAO_RECUSADA,
    "ExecucaoCancelada": CancelReason.EXECUCAO_CANCELADA,
}

# Evento que confirma cada compensação.
_COMPENSATION_DONE: dict[str, SagaStep] = {
    "PecasLiberadas": SagaStep.LIBERAR_PECAS,
    "CobrancaCancelada": SagaStep.CANCELAR_COBRANCA,
}

KNOWN_EVENTS = frozenset(EVENT_STEP) | frozenset(_COMPENSATION_DONE)


@dataclass
class SagaStepRecord:
    step: SagaStep
    status: StepStatus = StepStatus.PENDENTE
    attempts: int = 0
    last_sent_at: datetime | None = None
    finished_at: datetime | None = None
    id: int | None = None

    @property
    def command(self) -> str | None:
        return STEP_COMMAND.get(self.step)


@dataclass
class Decision:
    """O que o orquestrador deve fazer depois de um evento."""

    commands: list[str] = field(default_factory=list)
    order_transitions: list[tuple[ServiceOrderStatus, str | None]] = field(default_factory=list)
    ignored: bool = False
    note: str | None = None


@dataclass
class Saga:
    order_id: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: SagaState = SagaState.EM_ANDAMENTO
    current_step: SagaStep | None = None
    failure_reason: str | None = None
    steps: dict[SagaStep, SagaStepRecord] = field(default_factory=dict)
    started_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None

    # ─── Início ─────────────────────────────────────────────────────────────
    @classmethod
    def start(cls, order_id: int, now: datetime | None = None) -> tuple["Saga", Decision]:
        moment = now or datetime.now(UTC)
        saga = cls(order_id=order_id, started_at=moment, updated_at=moment)
        command = saga._begin(SagaStep.RESERVAR_PECAS, moment)
        return saga, Decision(commands=[command])

    # ─── Eventos ────────────────────────────────────────────────────────────
    def apply(
        self,
        event_type: str,
        now: datetime | None = None,
        *,
        order_status: ServiceOrderStatus | None = None,
    ) -> Decision:
        """Decide o que fazer com o evento.

        `order_status` é o status atual da OS: ordena os eventos de andamento da
        execução, que pertencem à mesma etapa. Levanta `OutOfOrderEventError`, sem
        mudar nada, quando o evento chega antes do que o precede.
        """
        moment = now or datetime.now(UTC)

        if event_type == "PagamentoConfirmado" and self.state in (
            SagaState.COMPENSANDO,
            SagaState.COMPENSADA,
        ):
            return self._late_payment(moment)
        if event_type in _COMPENSATION_DONE:
            return self._compensation_done(_COMPENSATION_DONE[event_type], moment)
        if event_type not in EVENT_STEP:
            return Decision(ignored=True, note=f"evento desconhecido: {event_type}")
        if self.state != SagaState.EM_ANDAMENTO:
            return Decision(ignored=True, note=f"Saga {self.state.value}: {event_type} ignorado")
        repeated = self._check_order(event_type, order_status)
        if repeated:
            return Decision(ignored=True, note=repeated)

        self.updated_at = moment
        if event_type in _FAILURE:
            return self._fail(_FAILURE[event_type], moment)
        if event_type in _PROGRESS:
            return Decision(order_transitions=[(_PROGRESS[event_type], None)])
        return self._advance(event_type, moment)

    def _check_order(self, event_type: str, order_status: ServiceOrderStatus | None) -> str | None:
        """Devolve o motivo para ignorar um evento repetido; levanta se ele chegou cedo demais."""
        step = EVENT_STEP[event_type]
        if step != self.current_step:
            if _STEP_ORDER.index(step) > _STEP_ORDER.index(self.current_step):
                raise OutOfOrderEventError(event_type, f"a Saga ainda está em {self.current_step.value}")
            return f"{event_type} de etapa já concluída (atual: {self.current_step.value})"

        target = _EXECUTION_TARGET.get(event_type)
        if target is None or order_status not in _EXECUTION_ORDER:
            return None
        gap = _EXECUTION_ORDER.index(target) - _EXECUTION_ORDER.index(order_status)
        if gap <= 0:
            return f"{event_type} repetido: a OS já está em {order_status.value}"
        if gap > 1:
            raise OutOfOrderEventError(event_type, f"a OS ainda está em {order_status.value}")
        return None

    def _advance(self, event_type: str, moment: datetime) -> Decision:
        self._finish(self.current_step, StepStatus.CONCLUIDA, moment)
        next_step, order_status = _ADVANCE[event_type]
        decision = Decision(order_transitions=[(order_status, None)] if order_status else [])

        if next_step is None:
            self.state = SagaState.CONCLUIDA
            self.current_step = None
            self.finished_at = moment
            return decision

        command = self._begin(next_step, moment)
        if command:
            decision.commands.append(command)
        return decision

    def _fail(self, reason: CancelReason, moment: datetime) -> Decision:
        self._finish(self.current_step, StepStatus.FALHOU, moment)
        self.state = SagaState.COMPENSANDO
        self.failure_reason = reason.value
        decision = Decision(order_transitions=[(ServiceOrderStatus.EM_CANCELAMENTO, reason.value)])

        # Só se desfaz o que foi feito: reserva confirmada e orçamento gerado.
        compensations = []
        if self._concluded(SagaStep.RESERVAR_PECAS):
            compensations.append(SagaStep.LIBERAR_PECAS)
        if self._concluded(SagaStep.GERAR_ORCAMENTO):
            compensations.append(SagaStep.CANCELAR_COBRANCA)

        for step in compensations:
            decision.commands.append(self._begin(step, moment, make_current=False))

        if not compensations:
            self._close_compensated(decision, moment)
        return decision

    def _compensation_done(self, step: SagaStep, moment: datetime) -> Decision:
        record = self.steps.get(step)
        if self.state != SagaState.COMPENSANDO or record is None or record.status != StepStatus.PENDENTE:
            return Decision(ignored=True, note=f"confirmação de {step.value} sem compensação pendente")

        self._finish(step, StepStatus.CONCLUIDA, moment)
        self.updated_at = moment
        decision = Decision()
        if not self._pending_compensations():
            self._close_compensated(decision, moment)
        return decision

    def _late_payment(self, moment: datetime) -> Decision:
        """Pagamento que chega com a Saga já desfazendo: a cobrança é cancelada de novo (estorno)."""
        self.updated_at = moment
        command = self._begin(SagaStep.CANCELAR_COBRANCA, moment, make_current=False)
        if self.state == SagaState.COMPENSADA:
            # A OS já está cancelada: só garante o estorno, sem reabrir a Saga.
            self._finish(SagaStep.CANCELAR_COBRANCA, StepStatus.CONCLUIDA, moment)
        return Decision(commands=[command], note="pagamento tardio: estorno solicitado")

    def _close_compensated(self, decision: Decision, moment: datetime) -> None:
        self.state = SagaState.COMPENSADA
        self.finished_at = moment
        decision.order_transitions.append((ServiceOrderStatus.CANCELADA, None))

    # ─── Reenvio de etapas paradas ──────────────────────────────────────────
    def stalled_steps(self, now: datetime, after: timedelta) -> list[SagaStepRecord]:
        """Etapas com comando enviado e sem resposta há mais de `after`."""
        return [
            record
            for record in self.steps.values()
            if record.status == StepStatus.PENDENTE
            and record.command is not None
            and record.last_sent_at is not None
            and _aware(now) - _aware(record.last_sent_at) >= after
        ]

    def mark_resent(self, record: SagaStepRecord, now: datetime) -> str:
        record.attempts += 1
        record.last_sent_at = now
        self.updated_at = now
        return record.command

    # ─── Auxiliares ─────────────────────────────────────────────────────────
    def _begin(self, step: SagaStep, moment: datetime, make_current: bool = True) -> str | None:
        record = self.steps.get(step) or SagaStepRecord(step=step)
        record.status = StepStatus.PENDENTE
        record.finished_at = None
        record.attempts += 1 if step in STEP_COMMAND else 0
        record.last_sent_at = moment if step in STEP_COMMAND else None
        self.steps[step] = record
        if make_current:
            self.current_step = step
        return record.command

    def _finish(self, step: SagaStep, status: StepStatus, moment: datetime) -> None:
        record = self.steps[step]
        record.status = status
        record.finished_at = moment

    def _concluded(self, step: SagaStep) -> bool:
        record = self.steps.get(step)
        return record is not None and record.status == StepStatus.CONCLUIDA

    def _pending_compensations(self) -> list[SagaStep]:
        return [
            step
            for step in (SagaStep.LIBERAR_PECAS, SagaStep.CANCELAR_COBRANCA)
            if step in self.steps and self.steps[step].status == StepStatus.PENDENTE
        ]


def _aware(moment: datetime) -> datetime:
    """O SQLite devolve horários sem fuso; a aplicação sempre grava em UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
