from typing import Protocol

from app.domain.entities.service_order import ServiceOrder
from app.domain.value_objects.service_order_status import ServiceOrderStatus


class ServiceOrderRepositoryPort(Protocol):
    """Contrato de persistência da Ordem de Serviço (opera em entidades de domínio)."""

    async def add(self, order: ServiceOrder, commit: bool = True) -> ServiceOrder: ...

    async def get_by_id(self, order_id: int) -> ServiceOrder | None: ...

    async def list_active(self, status: ServiceOrderStatus | None = None) -> list[ServiceOrder]: ...

    async def update(self, order: ServiceOrder, commit: bool = True) -> ServiceOrder: ...

    async def get_completed_with_times(self) -> list[ServiceOrder]: ...
