"""Persistência da Saga, do outbox e das mensagens processadas.

Nenhum destes repositórios faz commit: quem decide é a unidade de trabalho, para
que tudo de uma mesma decisão seja gravado na mesma transação.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.messages import Envelope
from app.application.ports.saga import InboxPort, OutboxPort, SagaRepositoryPort, UnitOfWorkPort
from app.domain.entities.saga import Saga, SagaState, SagaStepRecord
from app.infrastructure.observability.tracing import dump_context
from app.infrastructure.persistence.models.saga_model import (
    OutboxMessage,
    ProcessedMessage,
    SagaInstance,
    SagaStepModel,
)


def _to_domain(model: SagaInstance) -> Saga:
    return Saga(
        id=model.id,
        order_id=model.order_id,
        state=model.state,
        current_step=model.current_step,
        failure_reason=model.failure_reason,
        started_at=model.started_at,
        updated_at=model.updated_at,
        finished_at=model.finished_at,
        steps={
            step.step: SagaStepRecord(
                id=step.id,
                step=step.step,
                status=step.status,
                attempts=step.attempts,
                last_sent_at=step.last_sent_at,
                finished_at=step.finished_at,
            )
            for step in model.steps
        },
    )


class SqlAlchemySagaRepository(SagaRepositoryPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def _get_model(self, lock: bool = False, **filters) -> SagaInstance | None:
        query = select(SagaInstance).options(selectinload(SagaInstance.steps)).filter_by(**filters)
        if lock:
            # Duas confirmações da mesma Saga processadas ao mesmo tempo (dois pods)
            # não podem ler o mesmo estado: a segunda espera a primeira terminar.
            query = query.with_for_update()
        result = await self._db.execute(query.execution_options(populate_existing=True))
        return result.scalar_one_or_none()

    async def add(self, saga: Saga) -> None:
        model = SagaInstance(id=saga.id, order_id=saga.order_id, steps=[])
        self._db.add(model)
        self._copy(saga, model)
        await self._db.flush()

    async def get_by_order(self, order_id: int, lock: bool = False) -> Saga | None:
        model = await self._get_model(lock=lock, order_id=order_id)
        return _to_domain(model) if model else None

    async def save(self, saga: Saga) -> None:
        model = await self._get_model(id=saga.id)
        self._copy(saga, model)
        await self._db.flush()

    async def list_running(self) -> list[Saga]:
        result = await self._db.execute(
            select(SagaInstance)
            .options(selectinload(SagaInstance.steps))
            .where(SagaInstance.state.in_([SagaState.EM_ANDAMENTO, SagaState.COMPENSANDO]))
            .order_by(SagaInstance.started_at)
        )
        return [_to_domain(model) for model in result.scalars().all()]

    @staticmethod
    def _copy(saga: Saga, model: SagaInstance) -> None:
        model.state = saga.state
        model.current_step = saga.current_step
        model.failure_reason = saga.failure_reason
        model.started_at = saga.started_at or model.started_at
        model.updated_at = saga.updated_at or datetime.now(UTC)
        model.finished_at = saga.finished_at

        existing = {step.step: step for step in model.steps}
        for record in saga.steps.values():
            step_model = existing.get(record.step)
            if step_model is None:
                step_model = SagaStepModel(step=record.step)
                model.steps.append(step_model)
            step_model.status = record.status
            step_model.attempts = record.attempts
            step_model.last_sent_at = record.last_sent_at
            step_model.finished_at = record.finished_at


class SqlAlchemyOutbox(OutboxPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def add(self, envelope: Envelope) -> None:
        self._db.add(
            OutboxMessage(
                message_id=envelope.message_id,
                message_type=envelope.type,
                exchange=envelope.exchange,
                routing_key=envelope.routing_key,
                body=envelope.to_json(),
                trace_context=dump_context(),
            )
        )

    async def pending(self, limit: int = 100) -> list[OutboxMessage]:
        result = await self._db.execute(
            select(OutboxMessage)
            .where(OutboxMessage.published_at.is_(None))
            .order_by(OutboxMessage.id)
            .limit(limit)
            # Cada réplica publica só o que nenhuma outra pegou.
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars().all())


class SqlAlchemyInbox(InboxPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def already_processed(self, message_id: str) -> bool:
        return await self._db.get(ProcessedMessage, message_id) is not None

    async def mark_processed(self, message_id: str) -> None:
        self._db.add(ProcessedMessage(message_id=message_id))


class SqlAlchemyUnitOfWork(UnitOfWorkPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def commit(self) -> None:
        await self._db.commit()

    async def rollback(self) -> None:
        await self._db.rollback()
