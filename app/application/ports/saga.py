"""Portas usadas pelo orquestrador da Saga.

Todas operam na mesma transação: mudar o status da OS, avançar a Saga, gravar o
próximo comando no outbox e registrar a mensagem como processada só ficam
valendo juntos, no `commit` da unidade de trabalho.
"""

from typing import Protocol

from app.application.messages import Envelope
from app.domain.entities.saga import Saga


class SagaRepositoryPort(Protocol):
    async def add(self, saga: Saga) -> None: ...

    async def get_by_order(self, order_id: int, lock: bool = False) -> Saga | None: ...

    async def save(self, saga: Saga) -> None: ...

    async def list_running(self) -> list[Saga]:
        """Sagas em andamento ou compensando (candidatas a reenvio de comando)."""
        ...


class OutboxPort(Protocol):
    async def add(self, envelope: Envelope) -> None: ...


class InboxPort(Protocol):
    """Mensagens já processadas, para descartar entregas repetidas."""

    async def already_processed(self, message_id: str) -> bool: ...

    async def mark_processed(self, message_id: str) -> None: ...


class UnitOfWorkPort(Protocol):
    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
