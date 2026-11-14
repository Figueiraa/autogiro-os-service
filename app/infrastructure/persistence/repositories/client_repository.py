from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.client_repository import ClientRepositoryPort
from app.domain.entities.client import Client
from app.domain.exceptions import ConflictError
from app.infrastructure.persistence.mappers.client_mapper import to_domain
from app.infrastructure.persistence.models.client_model import Client as ClientModel
from app.infrastructure.persistence.models.service_order_model import ServiceOrder as ServiceOrderModel
from app.infrastructure.persistence.models.vehicle_model import Vehicle as VehicleModel


class SqlAlchemyClientRepository(ClientRepositoryPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def _get_model(self, client_id: int) -> ClientModel | None:
        result = await self._db.execute(select(ClientModel).where(ClientModel.id == client_id))
        return result.scalar_one_or_none()

    async def get_all(self) -> list[Client]:
        result = await self._db.execute(select(ClientModel).order_by(ClientModel.name))
        return [to_domain(model) for model in result.scalars().all()]

    async def get_by_id(self, client_id: int) -> Client | None:
        model = await self._get_model(client_id)
        return to_domain(model) if model else None

    async def get_by_cpf_cnpj(self, cpf_cnpj: str) -> Client | None:
        result = await self._db.execute(select(ClientModel).where(ClientModel.cpf_cnpj == cpf_cnpj))
        model = result.scalar_one_or_none()
        return to_domain(model) if model else None

    async def add(self, client: Client) -> Client:
        model = ClientModel(
            name=client.name,
            cpf_cnpj=client.cpf_cnpj,
            phone=client.phone,
            email=client.email,
            address=client.address,
        )
        self._db.add(model)
        await self._db.commit()
        await self._db.refresh(model)
        return to_domain(model)

    async def update(self, client: Client) -> Client:
        model = await self._get_model(client.id)
        model.name = client.name
        model.phone = client.phone
        model.email = client.email
        model.address = client.address
        await self._db.commit()
        await self._db.refresh(model)
        return to_domain(model)

    async def is_referenced(self, client_id: int) -> bool:
        query = select(
            or_(
                exists().where(VehicleModel.client_id == client_id),
                exists().where(ServiceOrderModel.client_id == client_id),
            )
        )
        return bool((await self._db.execute(query)).scalar())

    async def delete(self, client_id: int) -> None:
        model = await self._get_model(client_id)
        if model is not None:
            await self._db.delete(model)
            try:
                await self._db.commit()
            except IntegrityError as exc:
                # Vínculo criado entre a verificação do caso de uso e a exclusão.
                await self._db.rollback()
                raise ConflictError(
                    f"Cliente {client_id} tem registros vinculados e não pode ser excluído"
                ) from exc
