"""Casos de uso da Ordem de Serviço.

A abertura não calcula orçamento nem mexe em estoque: isso é feito pela Saga,
que reserva as peças no Execution Service e gera o orçamento no Billing Service.
"""

import logging
from datetime import UTC, datetime

from app.application.dtos.service_order_dtos import AverageExecutionTimeResult, OpenServiceOrderInput
from app.application.ports.client_repository import ClientRepositoryPort
from app.application.ports.notifier import NotifierPort
from app.application.ports.part_catalog import PartCatalogPort, PartCatalogUnavailableError
from app.application.ports.service_order_repository import ServiceOrderRepositoryPort
from app.application.ports.service_type_repository import ServiceTypeRepositoryPort
from app.application.ports.vehicle_repository import VehicleRepositoryPort
from app.domain.entities.service_order import ServiceOrder, ServiceOrderItem, ServiceOrderPart
from app.domain.exceptions import BusinessRuleError, NotFoundError
from app.domain.value_objects.service_order_status import ServiceOrderStatus

logger = logging.getLogger(__name__)


class OpenServiceOrderUseCase:
    """Abre a OS a partir do veículo, dos serviços do catálogo e das peças pedidas."""

    def __init__(
        self,
        repository: ServiceOrderRepositoryPort,
        vehicles: VehicleRepositoryPort,
        service_types: ServiceTypeRepositoryPort,
        part_catalog: PartCatalogPort,
    ):
        self._repo = repository
        self._vehicles = vehicles
        self._service_types = service_types
        self._parts = part_catalog

    async def execute(self, data: OpenServiceOrderInput) -> ServiceOrder:
        vehicle = await self._vehicles.get_by_id(data.vehicle_id)
        if not vehicle:
            raise NotFoundError("Veículo", data.vehicle_id)

        items = []
        for item in data.items:
            service_type = await self._service_types.get_by_id(item.service_type_id)
            if not service_type:
                raise NotFoundError("Serviço", item.service_type_id)
            items.append(
                ServiceOrderItem(
                    service_type_id=service_type.id,
                    name=service_type.name,
                    quantity=item.quantity,
                    unit_price=float(service_type.price),
                )
            )

        await self._validate_parts([part.part_id for part in data.parts])
        parts = [ServiceOrderPart(part_id=part.part_id, quantity=part.quantity) for part in data.parts]

        now = datetime.now(UTC)
        number = f"OS{now.strftime('%Y%m%d')}{await self._repo.count() + 1:04d}"
        order = ServiceOrder.open(
            number=number,
            vehicle_id=vehicle.id,
            client_id=vehicle.client_id,
            items=items,
            parts=parts,
            notes=data.notes,
            now=now,
        )
        return await self._repo.add(order)

    async def _validate_parts(self, part_ids: list[int]) -> None:
        """Avisa na hora se uma peça não existe; se o catálogo estiver fora, segue em frente."""
        for part_id in part_ids:
            try:
                part = await self._parts.find(part_id)
            except PartCatalogUnavailableError:
                logger.warning(
                    "Catálogo de peças indisponível; a validação das peças fica com a Saga"
                )
                return
            if part is None:
                raise BusinessRuleError(f"Peça {part_id} não existe no catálogo")


class GetServiceOrderUseCase:
    def __init__(self, repository: ServiceOrderRepositoryPort):
        self._repo = repository

    async def execute(self, order_id: int) -> ServiceOrder:
        order = await self._repo.get_by_id(order_id)
        if not order:
            raise NotFoundError("Ordem de serviço", order_id)
        return order


class ListServiceOrdersUseCase:
    """OS ativas, ordenadas pelo que exige ação da oficina primeiro."""

    def __init__(self, repository: ServiceOrderRepositoryPort):
        self._repo = repository

    async def execute(self, status: ServiceOrderStatus | None = None) -> list[ServiceOrder]:
        return await self._repo.list_active(status)


class DeliverServiceOrderUseCase:
    """Entrega o veículo: a única mudança de status manual (`FINALIZADA → ENTREGUE`)."""

    def __init__(
        self,
        repository: ServiceOrderRepositoryPort,
        clients: ClientRepositoryPort,
        notifier: NotifierPort,
    ):
        self._repo = repository
        self._clients = clients
        self._notifier = notifier

    async def execute(self, order_id: int) -> tuple[ServiceOrder, float | None]:
        order = await self._repo.get_by_id(order_id)
        if not order:
            raise NotFoundError("Ordem de serviço", order_id)

        _, seconds_in_previous = order.transition_to(ServiceOrderStatus.ENTREGUE)
        updated = await self._repo.update(order)

        client = await self._clients.get_by_id(updated.client_id)
        await self._notifier.notify_status_change(
            client.email if client else None, updated.number, updated.status.value
        )
        return updated, seconds_in_previous


class GetAverageExecutionTimeUseCase:
    """Tempo médio entre o início do reparo e a finalização, nas OS já entregues."""

    def __init__(self, repository: ServiceOrderRepositoryPort):
        self._repo = repository

    async def execute(self) -> AverageExecutionTimeResult:
        orders = await self._repo.get_completed_with_times()
        if not orders:
            return AverageExecutionTimeResult(average_minutes=None, total_completed=0)

        total_minutes = sum(
            (order.completed_at - order.started_at).total_seconds() / 60 for order in orders
        )
        return AverageExecutionTimeResult(
            average_minutes=round(total_minutes / len(orders), 2),
            total_completed=len(orders),
        )
