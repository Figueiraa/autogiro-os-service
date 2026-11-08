from app.domain.entities.service_order import (
    ServiceOrder,
    ServiceOrderItem,
    ServiceOrderPart,
    StatusChange,
)
from app.infrastructure.persistence.models.service_order_model import (
    ServiceOrder as ServiceOrderModel,
)
from app.infrastructure.persistence.models.service_order_model import (
    ServiceOrderItem as ServiceOrderItemModel,
)
from app.infrastructure.persistence.models.service_order_model import (
    ServiceOrderPart as ServiceOrderPartModel,
)
from app.infrastructure.persistence.models.service_order_model import (
    ServiceOrderStatusHistory as StatusHistoryModel,
)


def _money(value) -> float | None:
    return float(value) if value is not None else None


def to_domain(model: ServiceOrderModel) -> ServiceOrder:
    return ServiceOrder(
        id=model.id,
        number=model.number,
        vehicle_id=model.vehicle_id,
        client_id=model.client_id,
        status=model.status,
        notes=model.notes,
        budget_total=_money(model.budget_total),
        payment_link=model.payment_link,
        cancel_reason=model.cancel_reason,
        created_at=model.created_at,
        updated_at=model.updated_at,
        started_at=model.started_at,
        completed_at=model.completed_at,
        delivered_at=model.delivered_at,
        items=[
            ServiceOrderItem(
                id=item.id,
                service_type_id=item.service_type_id,
                name=item.name,
                quantity=item.quantity,
                unit_price=float(item.unit_price),
            )
            for item in model.items
        ],
        parts=[
            ServiceOrderPart(
                id=part.id,
                part_id=part.part_id,
                quantity=part.quantity,
                name=part.part_name,
                unit_price=_money(part.unit_price),
            )
            for part in model.parts
        ],
        history=[history_to_domain(entry) for entry in model.history],
    )


def history_to_domain(entry: StatusHistoryModel) -> StatusChange:
    return StatusChange(
        id=entry.id,
        from_status=entry.from_status,
        to_status=entry.to_status,
        reason=entry.reason,
        source=entry.source,
        occurred_at=entry.occurred_at,
    )


def history_to_model(change: StatusChange) -> StatusHistoryModel:
    return StatusHistoryModel(
        from_status=change.from_status,
        to_status=change.to_status,
        reason=change.reason,
        source=change.source,
        occurred_at=change.occurred_at,
    )


def to_model(order: ServiceOrder) -> ServiceOrderModel:
    return ServiceOrderModel(
        number=order.number,
        vehicle_id=order.vehicle_id,
        client_id=order.client_id,
        status=order.status,
        notes=order.notes,
        budget_total=order.budget_total,
        payment_link=order.payment_link,
        cancel_reason=order.cancel_reason,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=[
            ServiceOrderItemModel(
                service_type_id=item.service_type_id,
                name=item.name,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in order.items
        ],
        parts=[
            ServiceOrderPartModel(
                part_id=part.part_id,
                quantity=part.quantity,
                part_name=part.name,
                unit_price=part.unit_price,
            )
            for part in order.parts
        ],
        history=[history_to_model(change) for change in order.history],
    )
