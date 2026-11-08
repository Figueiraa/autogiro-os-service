"""Montagem do orquestrador da Saga com os adaptadores de uma sessão do banco."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.use_cases.saga_orchestrator import SagaOrchestrator
from app.infrastructure.config import settings
from app.infrastructure.notifications.notifier import build_notifier
from app.infrastructure.persistence.repositories.client_repository import SqlAlchemyClientRepository
from app.infrastructure.persistence.repositories.saga_repository import (
    SqlAlchemyInbox,
    SqlAlchemyOutbox,
    SqlAlchemySagaRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.persistence.repositories.service_order_repository import (
    SqlAlchemyServiceOrderRepository,
)
from app.infrastructure.persistence.repositories.vehicle_repository import SqlAlchemyVehicleRepository


def build_orchestrator(db: AsyncSession, *, notify: bool = True) -> SagaOrchestrator:
    return SagaOrchestrator(
        orders=SqlAlchemyServiceOrderRepository(db),
        sagas=SqlAlchemySagaRepository(db),
        outbox=SqlAlchemyOutbox(db),
        inbox=SqlAlchemyInbox(db),
        uow=SqlAlchemyUnitOfWork(db),
        clients=SqlAlchemyClientRepository(db),
        vehicles=SqlAlchemyVehicleRepository(db),
        notifier=build_notifier() if notify else None,
        step_retry_after=timedelta(seconds=settings.SAGA_STEP_RETRY_SECONDS),
    )
