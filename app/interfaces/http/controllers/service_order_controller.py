from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dtos.service_order_dtos import (
    OpenServiceOrderInput,
    OpenServiceOrderItemInput,
    OpenServiceOrderPartInput,
)
from app.application.ports.part_catalog import PartCatalogPort
from app.application.use_cases.service_order import (
    DeliverServiceOrderUseCase,
    GetAverageExecutionTimeUseCase,
    GetServiceOrderUseCase,
    ListServiceOrdersUseCase,
    OpenServiceOrderUseCase,
)
from app.domain.exceptions import NotFoundError
from app.domain.value_objects.service_order_status import ServiceOrderStatus
from app.infrastructure.composition import build_orchestrator
from app.infrastructure.config import settings
from app.infrastructure.database import get_db
from app.infrastructure.gateways.part_catalog import DisabledPartCatalog, HttpPartCatalog
from app.infrastructure.notifications.notifier import build_notifier
from app.infrastructure.observability.metrics import (
    record_service_order_opened,
    record_status_duration,
    record_status_transition,
)
from app.infrastructure.persistence.repositories.client_repository import SqlAlchemyClientRepository
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
from app.infrastructure.security.jwt_tokens import Principal
from app.interfaces.http.dependencies import get_current_principal
from app.interfaces.http.schemas.service_order_schema import (
    AverageExecutionTimeResponse,
    SagaResponse,
    ServiceOrderCreate,
    ServiceOrderPublicStatus,
    ServiceOrderResponse,
    ServiceOrderSummary,
    StatusChangeResponse,
)

router = APIRouter(prefix="/service-orders", tags=["Ordens de Serviço"])


def _repository(db: AsyncSession) -> SqlAlchemyServiceOrderRepository:
    return SqlAlchemyServiceOrderRepository(db)


def get_part_catalog(principal: Principal = Depends(get_current_principal)) -> PartCatalogPort:
    """Catálogo de peças do Execution Service, chamado com o token de quem abre a OS."""
    if not settings.EXECUTION_SERVICE_URL:
        return DisabledPartCatalog()
    return HttpPartCatalog(
        settings.EXECUTION_SERVICE_URL, principal.token, settings.EXECUTION_SERVICE_TIMEOUT_SECONDS
    )


@router.get("", response_model=list[ServiceOrderSummary])
async def list_orders(
    status: ServiceOrderStatus | None = Query(None, description="Filtra por um status específico"),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_principal),
):
    """OS ativas: em execução, aguardando aprovação, em diagnóstico e recebidas primeiro."""
    return await ListServiceOrdersUseCase(_repository(db)).execute(status)


@router.get("/metrics/average-execution-time", response_model=AverageExecutionTimeResponse)
async def average_execution_time(db: AsyncSession = Depends(get_db), _=Depends(get_current_principal)):
    return await GetAverageExecutionTimeUseCase(_repository(db)).execute()


@router.get("/{order_id}", response_model=ServiceOrderResponse)
async def get_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_principal)):
    return await GetServiceOrderUseCase(_repository(db)).execute(order_id)


@router.get("/{order_id}/history", response_model=list[StatusChangeResponse])
async def get_history(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_principal)):
    """Histórico de status da OS, com o motivo e a origem de cada mudança."""
    order = await GetServiceOrderUseCase(_repository(db)).execute(order_id)
    return order.history


@router.get("/{order_id}/saga", response_model=SagaResponse)
async def get_saga(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_principal)):
    """Estado da Saga da OS: etapa atual, situação de cada etapa e das compensações."""
    await GetServiceOrderUseCase(_repository(db)).execute(order_id)
    saga = await SqlAlchemySagaRepository(db).get_by_order(order_id)
    if saga is None:
        raise NotFoundError("Saga da ordem de serviço", order_id)
    return SagaResponse.from_domain(saga)


@router.post("", response_model=ServiceOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    data: ServiceOrderCreate,
    db: AsyncSession = Depends(get_db),
    part_catalog: PartCatalogPort = Depends(get_part_catalog),
    _=Depends(get_current_principal),
):
    use_case = OpenServiceOrderUseCase(
        _repository(db),
        SqlAlchemyVehicleRepository(db),
        SqlAlchemyServiceTypeRepository(db),
        part_catalog,
        build_orchestrator(db, notify=False),
        SqlAlchemyUnitOfWork(db),
    )
    order = await use_case.execute(
        OpenServiceOrderInput(
            vehicle_id=data.vehicle_id,
            notes=data.notes,
            items=[
                OpenServiceOrderItemInput(service_type_id=item.service_type_id, quantity=item.quantity)
                for item in data.items
            ],
            parts=[
                OpenServiceOrderPartInput(part_id=part.part_id, quantity=part.quantity) for part in data.parts
            ],
        )
    )
    # Métricas de negócio ficam no adapter: o caso de uso não depende de infraestrutura.
    record_service_order_opened()
    record_status_transition(order.status.value)
    return order


@router.post("/{order_id}/deliver", response_model=ServiceOrderResponse)
async def deliver_order(order_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_principal)):
    """Entrega o veículo ao cliente: `FINALIZADA → ENTREGUE`."""
    use_case = DeliverServiceOrderUseCase(_repository(db), SqlAlchemyClientRepository(db), build_notifier())
    order, seconds_in_previous = await use_case.execute(order_id)
    record_status_transition(order.status.value)
    if seconds_in_previous is not None:
        record_status_duration(ServiceOrderStatus.FINALIZADA.value, seconds_in_previous)
    return order


@router.get("/{order_id}/status", response_model=ServiceOrderPublicStatus)
async def get_order_status(order_id: int, db: AsyncSession = Depends(get_db)):
    """Rota pública: o cliente acompanha a OS sem autenticação."""
    order = await GetServiceOrderUseCase(_repository(db)).execute(order_id)
    return ServiceOrderPublicStatus(number=order.number, status=order.status)
