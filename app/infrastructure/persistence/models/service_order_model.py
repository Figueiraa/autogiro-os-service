from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.value_objects.service_order_status import ServiceOrderStatus
from app.infrastructure.database import Base

# Status gravado como texto (VARCHAR), não como tipo ENUM do PostgreSQL: incluir
# um status novo vira só uma migration de CHECK, sem ALTER TYPE.
_STATUS = Enum(ServiceOrderStatus, native_enum=False, length=30, validate_strings=True)


def _now() -> datetime:
    return datetime.now(UTC)


class ServiceOrder(Base):
    __tablename__ = "service_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    status: Mapped[ServiceOrderStatus] = mapped_column(
        _STATUS, nullable=False, default=ServiceOrderStatus.RECEBIDA
    )
    notes: Mapped[str | None] = mapped_column(Text)
    # Cópias vindas do Billing Service pela Saga.
    budget_total: Mapped[float | None] = mapped_column(Numeric(10, 2))
    payment_link: Mapped[str | None] = mapped_column(String(500))
    cancel_reason: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="service_orders")  # noqa: F821
    items: Mapped[list["ServiceOrderItem"]] = relationship(
        back_populates="service_order", cascade="all, delete-orphan", order_by="ServiceOrderItem.id"
    )
    parts: Mapped[list["ServiceOrderPart"]] = relationship(
        back_populates="service_order", cascade="all, delete-orphan", order_by="ServiceOrderPart.id"
    )
    history: Mapped[list["ServiceOrderStatusHistory"]] = relationship(
        back_populates="service_order",
        cascade="all, delete-orphan",
        # Ordem de gravação: é a sequência real das mudanças, imune a diferenças de
        # relógio entre os serviços que originam os eventos.
        order_by="ServiceOrderStatusHistory.id",
    )


class ServiceOrderItem(Base):
    __tablename__ = "service_order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_order_id: Mapped[int] = mapped_column(
        ForeignKey("service_orders.id", ondelete="CASCADE"), nullable=False
    )
    service_type_id: Mapped[int] = mapped_column(ForeignKey("service_types.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    service_order: Mapped[ServiceOrder] = relationship(back_populates="items")


class ServiceOrderPart(Base):
    """Peça pedida na OS. `part_id` é o id no Execution Service: não há FK."""

    __tablename__ = "service_order_parts"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_order_id: Mapped[int] = mapped_column(
        ForeignKey("service_orders.id", ondelete="CASCADE"), nullable=False
    )
    part_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    part_name: Mapped[str | None] = mapped_column(String(255))
    unit_price: Mapped[float | None] = mapped_column(Numeric(10, 2))

    service_order: Mapped[ServiceOrder] = relationship(back_populates="parts")


class ServiceOrderStatusHistory(Base):
    __tablename__ = "service_order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_order_id: Mapped[int] = mapped_column(
        ForeignKey("service_orders.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[ServiceOrderStatus | None] = mapped_column(_STATUS)
    to_status: Mapped[ServiceOrderStatus] = mapped_column(_STATUS, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    service_order: Mapped[ServiceOrder] = relationship(back_populates="history")
