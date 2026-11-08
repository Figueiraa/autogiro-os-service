from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.exceptions import BusinessRuleError, InvalidStatusTransitionError
from app.domain.value_objects.service_order_status import (
    VALID_TRANSITIONS,
    CancelReason,
    ServiceOrderStatus,
)

SOURCE_API = "api"


@dataclass
class ServiceOrderItem:
    """Serviço do catálogo pedido na OS, com nome e preço do momento da abertura."""

    service_type_id: int
    name: str
    quantity: int
    unit_price: float
    id: int | None = None

    @property
    def subtotal(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class ServiceOrderPart:
    """Peça pedida na OS.

    A peça pertence ao Execution Service: aqui fica só o que foi pedido. Nome e
    preço são cópias preenchidas quando a Saga confirma a reserva.
    """

    part_id: int
    quantity: int
    name: str | None = None
    unit_price: float | None = None
    id: int | None = None


@dataclass
class StatusChange:
    """Uma mudança de status, com motivo e origem (a API ou o evento que a causou)."""

    to_status: ServiceOrderStatus
    from_status: ServiceOrderStatus | None
    source: str
    occurred_at: datetime
    reason: str | None = None
    id: int | None = None


@dataclass
class ServiceOrder:
    """Entidade de domínio da Ordem de Serviço (Python puro, sem ORM)."""

    number: str
    vehicle_id: int
    client_id: int
    status: ServiceOrderStatus = ServiceOrderStatus.RECEBIDA
    notes: str | None = None
    items: list[ServiceOrderItem] = field(default_factory=list)
    parts: list[ServiceOrderPart] = field(default_factory=list)
    # Cópias vindas do Billing Service pela Saga.
    budget_total: float | None = None
    payment_link: str | None = None
    cancel_reason: str | None = None
    history: list[StatusChange] = field(default_factory=list)
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    delivered_at: datetime | None = None

    @classmethod
    def open(
        cls,
        *,
        number: str,
        vehicle_id: int,
        client_id: int,
        items: list[ServiceOrderItem],
        parts: list[ServiceOrderPart],
        notes: str | None = None,
        now: datetime | None = None,
    ) -> "ServiceOrder":
        """Abre a OS em `RECEBIDA`, já com o primeiro registro do histórico."""
        if not items:
            raise BusinessRuleError("A ordem de serviço precisa de pelo menos um serviço")

        moment = now or datetime.now(UTC)
        order = cls(
            number=number,
            vehicle_id=vehicle_id,
            client_id=client_id,
            notes=notes,
            items=items,
            parts=parts,
            created_at=moment,
            updated_at=moment,
        )
        order.history.append(
            StatusChange(
                to_status=ServiceOrderStatus.RECEBIDA,
                from_status=None,
                source=SOURCE_API,
                occurred_at=moment,
            )
        )
        return order

    @property
    def services_total(self) -> float:
        return round(sum(item.subtotal for item in self.items), 2)

    def transition_to(
        self,
        new_status: ServiceOrderStatus,
        *,
        source: str = SOURCE_API,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> tuple[StatusChange, float | None]:
        """Muda o status respeitando a máquina de estados e registra no histórico.

        Devolve a mudança registrada e há quantos segundos a OS estava no status
        anterior (alimenta a métrica de tempo por status; `None` sem marco anterior).
        """
        if new_status not in VALID_TRANSITIONS[self.status]:
            raise InvalidStatusTransitionError(self.status.value, new_status.value)

        if new_status == ServiceOrderStatus.EM_CANCELAMENTO:
            if reason not in {item.value for item in CancelReason}:
                raise BusinessRuleError(f"Motivo de cancelamento inválido: {reason!r}")
            self.cancel_reason = reason

        moment = now or datetime.now(UTC)
        previous = self.status
        seconds_in_previous = _seconds_between(self.updated_at or self.created_at, moment)

        if new_status == ServiceOrderStatus.EM_EXECUCAO:
            self.started_at = moment
        elif new_status == ServiceOrderStatus.FINALIZADA:
            self.completed_at = moment
        elif new_status == ServiceOrderStatus.ENTREGUE:
            self.delivered_at = moment

        self.status = new_status
        self.updated_at = moment
        change = StatusChange(
            to_status=new_status,
            from_status=previous,
            source=source,
            occurred_at=moment,
            reason=reason,
        )
        self.history.append(change)
        return change, seconds_in_previous


def _seconds_between(start: datetime | None, end: datetime) -> float | None:
    """Segundos entre dois instantes, tolerando timestamps sem fuso.

    O SQLite dos testes devolve datetimes *naive* e o PostgreSQL devolve *aware*
    (TIMESTAMPTZ). Os naive são assumidos em UTC, que é como a aplicação grava.
    """
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return max((end - start).total_seconds(), 0.0)
