from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.ports.service_order_repository import ServiceOrderRepositoryPort
from app.domain.entities.service_order import ServiceOrder
from app.domain.value_objects.service_order_status import (
    HIDDEN_FROM_ACTIVE_LIST,
    LIST_PRIORITY,
    ServiceOrderStatus,
)
from app.infrastructure.persistence.mappers.service_order_mapper import (
    history_to_model,
    to_domain,
    to_model,
)
from app.infrastructure.persistence.models.service_order_model import ServiceOrder as ServiceOrderModel

_PRIORITY = case(
    *((ServiceOrderModel.status == status, rank) for status, rank in LIST_PRIORITY.items()),
    else_=99,
)


class SqlAlchemyServiceOrderRepository(ServiceOrderRepositoryPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    @staticmethod
    def _with_relations():
        return select(ServiceOrderModel).options(
            selectinload(ServiceOrderModel.items),
            selectinload(ServiceOrderModel.parts),
            selectinload(ServiceOrderModel.history),
        )

    async def _get_model(self, order_id: int) -> ServiceOrderModel | None:
        # populate_existing: relê do banco mesmo se o objeto já estiver na sessão,
        # para devolver o histórico recém-gravado.
        result = await self._db.execute(
            self._with_relations()
            .where(ServiceOrderModel.id == order_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def add(self, order: ServiceOrder, commit: bool = True) -> ServiceOrder:
        """Grava a OS. Com `commit=False`, só envia ao banco (flush) e deixa a transação
        aberta, para a abertura da OS e o início da Saga ficarem na mesma transação."""
        model = to_model(order)
        self._db.add(model)
        await self._finish(commit)
        return to_domain(await self._get_model(model.id))

    async def get_by_id(self, order_id: int) -> ServiceOrder | None:
        model = await self._get_model(order_id)
        return to_domain(model) if model else None

    async def list_active(self, status: ServiceOrderStatus | None = None) -> list[ServiceOrder]:
        query = self._with_relations()
        if status is not None:
            query = query.where(ServiceOrderModel.status == status)
        else:
            query = query.where(ServiceOrderModel.status.notin_(HIDDEN_FROM_ACTIVE_LIST))
        query = query.order_by(_PRIORITY, ServiceOrderModel.created_at.asc(), ServiceOrderModel.id.asc())
        result = await self._db.execute(query)
        return [to_domain(model) for model in result.scalars().all()]

    async def update(self, order: ServiceOrder, commit: bool = True) -> ServiceOrder:
        model = await self._get_model(order.id)
        model.number = order.number
        model.status = order.status
        model.notes = order.notes
        model.budget_total = order.budget_total
        model.payment_link = order.payment_link
        model.cancel_reason = order.cancel_reason
        model.updated_at = order.updated_at
        model.started_at = order.started_at
        model.completed_at = order.completed_at
        model.delivered_at = order.delivered_at

        # Nome e preço das peças chegam depois da abertura, pela Saga.
        copies = {part.part_id: part for part in order.parts}
        for part_model in model.parts:
            copy = copies.get(part_model.part_id)
            if copy is not None:
                part_model.part_name = copy.name
                part_model.unit_price = copy.unit_price

        # O histórico só cresce: grava as mudanças que ainda não têm id.
        for change in order.history:
            if change.id is None:
                model.history.append(history_to_model(change))

        await self._finish(commit)
        return to_domain(await self._get_model(order.id))

    async def _finish(self, commit: bool) -> None:
        if commit:
            await self._db.commit()
        else:
            await self._db.flush()

    async def get_completed_with_times(self) -> list[ServiceOrder]:
        result = await self._db.execute(
            self._with_relations().where(
                ServiceOrderModel.status == ServiceOrderStatus.ENTREGUE,
                ServiceOrderModel.started_at.is_not(None),
                ServiceOrderModel.completed_at.is_not(None),
            )
        )
        return [to_domain(model) for model in result.scalars().all()]
