"""Orquestrador da Saga da ordem de serviço.

Executa as decisões do domínio (`Saga`): muda o status da OS, copia os dados que
os participantes devolvem, grava os próximos comandos no outbox e marca o evento
como processado — tudo numa transação. A notificação ao cliente sai depois do commit.

Evento que chega antes do que o precede não é marcado como processado: a exceção
volta ao consumidor, que o manda para a fila de retry.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.application.messages import Envelope
from app.application.ports.client_repository import ClientRepositoryPort
from app.application.ports.notifier import NotifierPort
from app.application.ports.saga import InboxPort, OutboxPort, SagaRepositoryPort, UnitOfWorkPort
from app.application.ports.service_order_repository import ServiceOrderRepositoryPort
from app.application.ports.vehicle_repository import VehicleRepositoryPort
from app.domain.entities.saga import Saga
from app.domain.entities.service_order import ServiceOrder
from app.domain.exceptions import InvalidStatusTransitionError, OutOfOrderEventError
from app.domain.value_objects.service_order_status import ServiceOrderStatus

logger = logging.getLogger("autogiro.saga")

APPLIED = "applied"
IGNORED = "ignored"
DUPLICATE = "duplicate"


@dataclass
class HandleResult:
    outcome: str
    commands: list[str] = field(default_factory=list)
    # Status aplicados na OS e há quantos segundos ela estava no anterior (para métricas).
    transitions: list[tuple[ServiceOrderStatus, float | None]] = field(default_factory=list)
    note: str | None = None
    # Estado da Saga depois do evento (para observabilidade).
    saga_state: str | None = None
    failure_reason: str | None = None


class SagaOrchestrator:
    def __init__(
        self,
        *,
        orders: ServiceOrderRepositoryPort,
        sagas: SagaRepositoryPort,
        outbox: OutboxPort,
        inbox: InboxPort,
        uow: UnitOfWorkPort,
        clients: ClientRepositoryPort,
        vehicles: VehicleRepositoryPort,
        notifier: NotifierPort | None = None,
        step_retry_after: timedelta = timedelta(seconds=120),
    ):
        self._orders = orders
        self._sagas = sagas
        self._outbox = outbox
        self._inbox = inbox
        self._uow = uow
        self._clients = clients
        self._vehicles = vehicles
        self._notifier = notifier
        self._retry_after = step_retry_after

    # ─── Início (chamado na abertura da OS, dentro da mesma transação) ───────
    async def start(self, order: ServiceOrder) -> Saga:
        saga, decision = Saga.start(order.id)
        await self._sagas.add(saga)
        await self._send(decision.commands, saga, order, causation_id=None)
        logger.info("Saga %s iniciada para a OS %s", saga.id, order.number)
        return saga

    # ─── Eventos dos participantes ───────────────────────────────────────────
    async def handle(self, envelope: Envelope) -> HandleResult:
        if await self._inbox.already_processed(envelope.message_id):
            return HandleResult(DUPLICATE, note="mensagem já processada")

        saga = await self._sagas.get_by_order(envelope.order_id, lock=True)
        if envelope.kind != "event" or saga is None or saga.id != envelope.saga_id:
            await self._inbox.mark_processed(envelope.message_id)
            await self._uow.commit()
            logger.warning("Mensagem %s ignorada: não pertence a uma Saga conhecida", envelope.type)
            return HandleResult(IGNORED, note="mensagem fora de uma Saga conhecida")

        order = await self._orders.get_by_id(envelope.order_id)
        try:
            decision = saga.apply(envelope.type, order_status=order.status)
        except OutOfOrderEventError as exc:
            # Solta a trava da Saga sem registrar o evento: ele volta pela fila de retry.
            await self._uow.rollback()
            logger.info("Saga %s: %s; nova tentativa mais tarde", saga.id, exc)
            raise
        result = HandleResult(IGNORED if decision.ignored else APPLIED, note=decision.note)

        if not decision.ignored:
            _copy_from_event(order, envelope)
            for status, reason in decision.order_transitions:
                try:
                    _, seconds = order.transition_to(status, source=envelope.type, reason=reason)
                except InvalidStatusTransitionError:
                    # Repetições e eventos adiantados já foram filtrados pela Saga: aqui a
                    # OS e a Saga discordam. Nada é gravado e o evento segue para retry/DLQ.
                    await self._uow.rollback()
                    logger.error(
                        "OS %s: transição %s → %s inválida em %s",
                        order.number, order.status.value, status.value, envelope.type,
                    )
                    raise
                result.transitions.append((status, seconds))
            await self._orders.update(order, commit=False)
            await self._sagas.save(saga)
            result.commands = await self._send(decision.commands, saga, order, envelope.message_id)
        else:
            logger.info("Saga %s: %s", saga.id, decision.note)

        result.saga_state = saga.state.value
        result.failure_reason = saga.failure_reason
        await self._inbox.mark_processed(envelope.message_id)
        await self._uow.commit()
        await self._notify(order, result.transitions)
        return result

    # ─── Reenvio de comandos sem resposta ────────────────────────────────────
    async def retry_stalled(self, now: datetime | None = None) -> int:
        """Reenvia comandos sem resposta há mais de `step_retry_after`.

        Cobre a perda de um evento entre o commit e a publicação no participante:
        pela idempotência por OS, ele não refaz o efeito, só republica o evento.
        """
        moment = now or datetime.now(UTC)
        resent = 0
        candidates = [
            snapshot.order_id
            for snapshot in await self._sagas.list_running()
            if snapshot.stalled_steps(moment, self._retry_after)
        ]
        await self._uow.commit()
        for order_id in candidates:
            resent += await self._retry_one(order_id, moment)
        return resent

    async def _retry_one(self, order_id: int, moment: datetime) -> int:
        """Reenvia as etapas paradas de uma Saga, relida com trava e numa transação própria.

        A listagem é só uma foto: entre ela e o reenvio, um evento pode ter avançado a
        Saga. Sem reler com trava, a foto velha seria gravada por cima do avanço.
        """
        saga = await self._sagas.get_by_order(order_id, lock=True)
        stalled = saga.stalled_steps(moment, self._retry_after) if saga else []
        if not stalled:
            await self._uow.commit()
            return 0
        order = await self._orders.get_by_id(saga.order_id)
        commands = [saga.mark_resent(record, moment) for record in stalled]
        await self._sagas.save(saga)
        await self._send(commands, saga, order, causation_id=None)
        await self._uow.commit()
        logger.warning("Saga %s: reenviando %s", saga.id, ", ".join(commands))
        return len(commands)

    # ─── Auxiliares ─────────────────────────────────────────────────────────
    async def _send(
        self, commands: list[str], saga: Saga, order: ServiceOrder, causation_id: str | None
    ) -> list[str]:
        for command in commands:
            payload = await self._payload(command, saga, order)
            await self._outbox.add(
                Envelope.command(
                    command,
                    saga_id=saga.id,
                    order_id=order.id,
                    payload=payload,
                    causation_id=causation_id,
                )
            )
        return list(commands)

    async def _payload(self, command: str, saga: Saga, order: ServiceOrder) -> dict:
        if command == "ReservarPecas":
            return {"items": [{"part_id": p.part_id, "quantity": p.quantity} for p in order.parts]}

        if command == "GerarOrcamento":
            client = await self._clients.get_by_id(order.client_id)
            return {
                "client": {"id": order.client_id, "name": client.name, "email": client.email},
                "services": [
                    {
                        "service_type_id": i.service_type_id,
                        "name": i.name,
                        "quantity": i.quantity,
                        "unit_price": i.unit_price,
                    }
                    for i in order.items
                ],
                "parts": [
                    {"part_id": p.part_id, "name": p.name, "quantity": p.quantity, "unit_price": p.unit_price}
                    for p in order.parts
                ],
            }

        if command == "EnfileirarExecucao":
            vehicle = await self._vehicles.get_by_id(order.vehicle_id)
            return {
                "vehicle": {"plate": vehicle.plate, "brand": vehicle.brand, "model": vehicle.model},
                "services": [
                    {"service_type_id": i.service_type_id, "name": i.name, "quantity": i.quantity}
                    for i in order.items
                ],
                "parts": [
                    {"part_id": p.part_id, "name": p.name, "quantity": p.quantity} for p in order.parts
                ],
                "notes": order.notes,
            }

        # Compensações: LiberarPecas e CancelarCobranca.
        return {"reason": saga.failure_reason or "PAGAMENTO_TARDIO"}

    async def _notify(
        self, order: ServiceOrder, transitions: list[tuple[ServiceOrderStatus, float | None]]
    ) -> None:
        if self._notifier is None or not transitions:
            return
        client = await self._clients.get_by_id(order.client_id)
        for status, _ in transitions:
            await self._notifier.notify_status_change(
                client.email if client else None, order.number, status.value
            )


def _copy_from_event(order: ServiceOrder, envelope: Envelope) -> None:
    """Guarda na OS as cópias dos dados que pertencem aos outros serviços."""
    payload = envelope.payload
    if envelope.type == "PecasReservadas":
        reserved = {item["part_id"]: item for item in payload.get("items", [])}
        for part in order.parts:
            item = reserved.get(part.part_id)
            if item is not None:
                part.name = item.get("name")
                part.unit_price = float(item["unit_price"]) if item.get("unit_price") is not None else None
    elif envelope.type == "OrcamentoGerado" and payload.get("total") is not None:
        order.budget_total = float(payload["total"])
    elif envelope.type == "OrcamentoAprovado":
        order.payment_link = payload.get("payment_link")
