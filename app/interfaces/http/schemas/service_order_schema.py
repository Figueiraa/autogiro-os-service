from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.value_objects.service_order_status import ServiceOrderStatus


class ServiceOrderItemInput(BaseModel):
    service_type_id: int
    quantity: int = Field(1, ge=1)


class ServiceOrderPartInput(BaseModel):
    part_id: int = Field(description="Id da peça no Execution Service")
    quantity: int = Field(1, ge=1)


class ServiceOrderCreate(BaseModel):
    vehicle_id: int
    notes: str | None = None
    items: list[ServiceOrderItemInput] = Field(min_length=1, description="Serviços do catálogo")
    parts: list[ServiceOrderPartInput] = Field(default_factory=list)


class ServiceOrderItemResponse(BaseModel):
    id: int
    service_type_id: int
    name: str
    quantity: int
    unit_price: float
    subtotal: float

    model_config = {"from_attributes": True}


class ServiceOrderPartResponse(BaseModel):
    id: int
    part_id: int
    quantity: int
    name: str | None = Field(description="Preenchido quando a reserva da peça é confirmada")
    unit_price: float | None = Field(description="Preenchido quando a reserva da peça é confirmada")

    model_config = {"from_attributes": True}


class StatusChangeResponse(BaseModel):
    from_status: ServiceOrderStatus | None
    to_status: ServiceOrderStatus
    reason: str | None
    source: str
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ServiceOrderResponse(BaseModel):
    id: int
    number: str
    vehicle_id: int
    client_id: int
    status: ServiceOrderStatus
    cancel_reason: str | None
    notes: str | None
    services_total: float
    budget_total: float | None = Field(description="Total do orçamento, quando gerado")
    payment_link: str | None = Field(description="Link de pagamento, quando o orçamento é aprovado")
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    delivered_at: datetime | None
    items: list[ServiceOrderItemResponse]
    parts: list[ServiceOrderPartResponse]

    model_config = {"from_attributes": True}


class ServiceOrderSummary(BaseModel):
    id: int
    number: str
    vehicle_id: int
    client_id: int
    status: ServiceOrderStatus
    budget_total: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ServiceOrderPublicStatus(BaseModel):
    number: str
    status: ServiceOrderStatus


class AverageExecutionTimeResponse(BaseModel):
    average_minutes: float | None
    total_completed: int

    model_config = {"from_attributes": True}
