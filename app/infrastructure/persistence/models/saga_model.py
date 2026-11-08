from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.entities.saga import SagaState, SagaStep, StepStatus
from app.infrastructure.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


def _text_enum(enum_type, length: int = 30) -> Enum:
    return Enum(enum_type, native_enum=False, length=length, validate_strings=True)


class SagaInstance(Base):
    __tablename__ = "saga_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"), unique=True, nullable=False)
    state: Mapped[SagaState] = mapped_column(_text_enum(SagaState), nullable=False)
    current_step: Mapped[SagaStep | None] = mapped_column(_text_enum(SagaStep))
    failure_reason: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["SagaStepModel"]] = relationship(
        back_populates="saga", cascade="all, delete-orphan", order_by="SagaStepModel.id"
    )


class SagaStepModel(Base):
    __tablename__ = "saga_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    saga_id: Mapped[str] = mapped_column(ForeignKey("saga_instances.id", ondelete="CASCADE"), nullable=False)
    step: Mapped[SagaStep] = mapped_column(_text_enum(SagaStep), nullable=False)
    status: Mapped[StepStatus] = mapped_column(_text_enum(StepStatus, 20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    saga: Mapped[SagaInstance] = relationship(back_populates="steps")


class OutboxMessage(Base):
    """Mensagem gravada na mesma transação da mudança de estado e publicada depois."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    message_type: Mapped[str] = mapped_column(String(60), nullable=False)
    exchange: Mapped[str] = mapped_column(String(60), nullable=False)
    routing_key: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedMessage(Base):
    """Mensagens já consumidas: a mesma entrega duas vezes produz efeito uma vez."""

    __tablename__ = "processed_messages"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
