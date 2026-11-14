"""Concorrência contra um PostgreSQL de verdade: travas, sequências e chaves estrangeiras.

O SQLite dos outros testes não tem `FOR UPDATE SKIP LOCKED` nem transações
simultâneas. Roda quando `TEST_POSTGRES_URL` está definida (no CI, um container do
PostgreSQL); sem ela, os testes são pulados.
"""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.dtos.service_order_dtos import (
    OpenServiceOrderInput,
    OpenServiceOrderItemInput,
    OpenServiceOrderPartInput,
)
from app.application.messages import Envelope
from app.application.ports.part_catalog import PartInfo
from app.application.use_cases.service_order import OpenServiceOrderUseCase
from app.application.use_cases.service_type import DeleteServiceTypeUseCase
from app.domain.entities.saga import SagaStep
from app.domain.exceptions import ConflictError
from app.infrastructure.composition import build_orchestrator
from app.infrastructure.messaging.runtime import publish_pending
from app.infrastructure.migrations import migrate
from app.infrastructure.persistence.models.saga_model import OutboxMessage
from app.infrastructure.persistence.repositories.saga_repository import (
    SqlAlchemySagaRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.persistence.repositories.service_order_repository import (
    SqlAlchemyServiceOrderRepository,
)
from app.infrastructure.persistence.repositories.service_type_repository import (
    SqlAlchemyServiceTypeRepository,
)
from app.infrastructure.persistence.repositories.vehicle_repository import SqlAlchemyVehicleRepository

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL não definida")


@pytest.fixture
async def fabrica():
    """Banco recriado pelas migrations (com os dados de demonstração) e uma fábrica de sessões."""
    engine = create_async_engine(POSTGRES_URL)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS auth CASCADE"))
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await migrate(POSTGRES_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _mensagem(ordem: int) -> OutboxMessage:
    message_id = str(uuid.uuid4())
    return OutboxMessage(
        message_id=message_id,
        message_type="ReservarPecas",
        exchange="autogiro.commands",
        routing_key="execution.reservar-pecas",
        body=json.dumps({"message_id": message_id, "ordem": ordem}),
    )


class TestOutboxComDuasReplicas:
    async def test_nenhuma_mensagem_e_publicada_duas_vezes(self, fabrica):
        async with fabrica() as db:
            db.add_all([_mensagem(i) for i in range(5)])
            await db.commit()
        publicadas: list[str] = []

        async def replica_b(exchange, routing_key, body, *, message_id, message_type, headers=None):
            publicadas.append(message_id)

        async def replica_a(exchange, routing_key, body, *, message_id, message_type, headers=None):
            publicadas.append(message_id)
            if len(publicadas) == 2:
                # A réplica B roda inteira enquanto a A ainda publica a segunda mensagem.
                await publish_pending(fabrica, replica_b)

        await publish_pending(fabrica, replica_a)

        assert len(publicadas) == 5
        assert len(set(publicadas)) == 5


class _CatalogoLivre:
    async def find(self, part_id: int) -> PartInfo | None:
        return PartInfo(id=part_id, name="Filtro de óleo", unit_price=32.5)


def _abertura(db, catalogo) -> OpenServiceOrderUseCase:
    return OpenServiceOrderUseCase(
        SqlAlchemyServiceOrderRepository(db),
        SqlAlchemyVehicleRepository(db),
        SqlAlchemyServiceTypeRepository(db),
        catalogo,
        build_orchestrator(db, notify=False),
        SqlAlchemyUnitOfWork(db),
    )


_PEDIDO = OpenServiceOrderInput(
    vehicle_id=1,
    items=[OpenServiceOrderItemInput(service_type_id=1)],
    parts=[OpenServiceOrderPartInput(part_id=3)],
)


class _CatalogoQueSegura:
    """Catálogo de peças que só responde quando as duas aberturas chegaram até ele."""

    def __init__(self):
        self._barreira = asyncio.Barrier(2)

    async def find(self, part_id: int) -> PartInfo | None:
        await self._barreira.wait()
        return PartInfo(id=part_id, name="Filtro de óleo", unit_price=32.5)


class TestAberturaSimultanea:
    async def test_duas_os_abertas_ao_mesmo_tempo_recebem_numeros_diferentes(self, fabrica):
        catalogo = _CatalogoQueSegura()

        async def abrir():
            async with fabrica() as db:
                return await _abertura(db, catalogo).execute(_PEDIDO)

        primeira, segunda = await asyncio.wait_for(asyncio.gather(abrir(), abrir()), timeout=30)

        assert primeira.number != segunda.number
        for ordem in (primeira, segunda):
            assert ordem.number.endswith(f"{ordem.id:04d}")


class TestReenvioComEventoEmAndamento:
    async def test_reenvio_espera_o_evento_e_nao_desfaz_o_avanco(self, fabrica):
        async with fabrica() as db:
            ordem = await _abertura(db, _CatalogoLivre()).execute(_PEDIDO)
            # A reserva de peças foi pedida há 10 minutos e ficou sem resposta.
            sagas = SqlAlchemySagaRepository(db)
            saga = await sagas.get_by_order(ordem.id)
            saga.steps[SagaStep.RESERVAR_PECAS].last_sent_at = datetime.now(UTC) - timedelta(minutes=10)
            await sagas.save(saga)
            await db.commit()

        async with fabrica() as consumidor:
            # O consumidor trava a Saga para processar PecasReservadas...
            await SqlAlchemySagaRepository(consumidor).get_by_order(ordem.id, lock=True)

            async def reenviar():
                async with fabrica() as db:
                    return await build_orchestrator(db, notify=False).retry_stalled()

            reenvio = asyncio.create_task(reenviar())
            await asyncio.sleep(0.5)
            # ...e o reenvio espera a trava, em vez de agir sobre a foto da listagem.
            assert not reenvio.done()
            evento = Envelope(
                type="PecasReservadas", kind="event", saga_id=saga.id, order_id=ordem.id, payload={"items": []}
            )
            await build_orchestrator(consumidor, notify=False).handle(evento)

        assert await asyncio.wait_for(reenvio, timeout=10) == 0
        async with fabrica() as db:
            saga = await SqlAlchemySagaRepository(db).get_by_order(ordem.id)
            tipos = (await db.execute(text("SELECT message_type FROM outbox ORDER BY id"))).scalars().all()
        assert saga.current_step == SagaStep.GERAR_ORCAMENTO
        assert tipos == ["ReservarPecas", "GerarOrcamento"]


class TestExclusaoComVinculo:
    async def test_chave_estrangeira_recusada_vira_conflito(self, fabrica):
        async with fabrica() as db:
            await db.execute(
                text(
                    "INSERT INTO service_orders (number, vehicle_id, client_id) "
                    "SELECT 'OS-TESTE', v.id, v.client_id FROM vehicles v WHERE v.id = 1"
                )
            )
            await db.execute(
                text(
                    "INSERT INTO service_order_items (service_order_id, service_type_id, name, unit_price) "
                    "SELECT id, 1, 'Troca de óleo', 149.9 FROM service_orders WHERE number = 'OS-TESTE'"
                )
            )
            await db.commit()
            servicos = SqlAlchemyServiceTypeRepository(db)

            # Sem a checagem do caso de uso (vínculo criado no meio), o RESTRICT do banco decide.
            with pytest.raises(ConflictError):
                await servicos.delete(1)
            with pytest.raises(ConflictError, match="já foi usado em ordens de serviço"):
                await DeleteServiceTypeUseCase(servicos).execute(1)
            assert await servicos.get_by_id(1) is not None
