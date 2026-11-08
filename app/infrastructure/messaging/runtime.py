"""Tarefas de mensageria que rodam junto com a API.

- consumidor da fila `os.saga-events`;
- publicador do outbox (a cada `OUTBOX_POLL_SECONDS`);
- reenvio de comandos sem resposta (a cada `SAGA_STALL_CHECK_SECONDS`).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.messages import EXCHANGE_EVENTS, Envelope
from app.application.use_cases.saga_orchestrator import HandleResult
from app.infrastructure.composition import build_orchestrator
from app.infrastructure.config import settings
from app.infrastructure.messaging.consumer import QueueConsumer
from app.infrastructure.messaging.rabbitmq import QUEUE_SAGA_EVENTS, SAGA_EVENT_BINDINGS, RabbitMQClient
from app.infrastructure.observability.metrics import record_status_duration, record_status_transition
from app.infrastructure.observability.tracing import load_context, publish_span
from app.infrastructure.persistence.repositories.saga_repository import SqlAlchemyOutbox

logger = logging.getLogger("autogiro.messaging")

Publish = Callable[..., Awaitable[None]]


async def publish_pending(
    session_factory: async_sessionmaker[AsyncSession], publish: Publish, limit: int = 100
) -> int:
    """Publica as mensagens pendentes do outbox, na ordem em que foram gravadas.

    Cada mensagem é marcada como publicada logo depois do publish confirmado. Se o
    processo cair entre os dois, ela é publicada de novo — os consumidores
    descartam a repetição pelo `message_id`.
    """
    published = 0
    async with session_factory() as db:
        for row in await SqlAlchemyOutbox(db).pending(limit):
            with publish_span(
                exchange=row.exchange,
                routing_key=row.routing_key,
                message_id=row.message_id,
                message_type=row.message_type,
                body=row.body,
                stored_context=load_context(row.trace_context),
            ) as headers:
                await publish(
                    row.exchange,
                    row.routing_key,
                    row.body.encode("utf-8"),
                    message_id=row.message_id,
                    message_type=row.message_type,
                    headers=headers,
                )
            row.published_at = datetime.now(UTC)
            await db.commit()
            published += 1
    return published


def tag_saga_result(result: HandleResult) -> None:
    """Estado da Saga e status da OS no span do consumo: base do painel da Saga."""
    span = trace.get_current_span()
    span.set_attribute("autogiro.saga.outcome", result.outcome)
    if result.saga_state:
        span.set_attribute("autogiro.saga.state", result.saga_state)
    if result.failure_reason:
        span.set_attribute("autogiro.saga.failure_reason", result.failure_reason)
    if result.transitions:
        span.set_attribute("autogiro.order.status", result.transitions[-1][0].value)


def handle_with(session_factory: async_sessionmaker[AsyncSession]) -> Callable[[Envelope], Awaitable[object]]:
    """Processa um evento da Saga numa sessão própria e registra as métricas das transições."""

    async def handle(envelope: Envelope) -> object:
        async with session_factory() as db:
            result = await build_orchestrator(db).handle(envelope)
        tag_saga_result(result)
        for status, seconds in result.transitions:
            record_status_transition(status.value)
            if seconds is not None:
                record_status_duration(status.value, seconds)
        return result

    return handle


async def retry_stalled(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as db:
        return await build_orchestrator(db, notify=False).retry_stalled()


async def run_every(interval: float, job: Callable[[], Awaitable[object]], name: str) -> None:
    """Executa `job` periodicamente até a tarefa ser cancelada; erros não derrubam o laço."""
    while True:
        try:
            await job()
        except asyncio.CancelledError:  # pragma: no cover - encerramento normal
            raise
        except Exception:
            logger.exception("Falha na tarefa periódica %s", name)
        await asyncio.sleep(interval)


class MessagingRuntime:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], client: RabbitMQClient | None = None
    ):
        self._session_factory = session_factory
        self.client = client or RabbitMQClient(
            settings.RABBITMQ_URL, retry_delay_ms=settings.MESSAGE_RETRY_DELAY_MS
        )
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        await self.client.connect()
        await self.client.declare_exchanges()
        queue = await self.client.declare_queue_with_retry(
            QUEUE_SAGA_EVENTS, EXCHANGE_EVENTS, SAGA_EVENT_BINDINGS
        )
        consumer = QueueConsumer(
            self.client, QUEUE_SAGA_EVENTS, handle_with(self._session_factory), settings.MESSAGE_MAX_RETRIES
        )
        await queue.consume(consumer)
        self._tasks = [
            asyncio.create_task(
                run_every(
                    settings.OUTBOX_POLL_SECONDS,
                    lambda: publish_pending(self._session_factory, self.client.publish),
                    "outbox",
                )
            ),
            asyncio.create_task(
                run_every(
                    settings.SAGA_STALL_CHECK_SECONDS,
                    lambda: retry_stalled(self._session_factory),
                    "reenvio de etapas paradas",
                )
            ),
        ]
        logger.info("Mensageria iniciada: consumindo %s", QUEUE_SAGA_EVENTS)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.client.close()

    async def is_up(self) -> bool:
        return self.client.is_connected
