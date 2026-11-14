from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports.service_type_repository import ServiceTypeRepositoryPort
from app.domain.entities.service_type import ServiceType
from app.domain.exceptions import ConflictError
from app.infrastructure.persistence.mappers.service_type_mapper import to_domain
from app.infrastructure.persistence.models.service_order_model import ServiceOrderItem
from app.infrastructure.persistence.models.service_type_model import ServiceType as ServiceTypeModel


class SqlAlchemyServiceTypeRepository(ServiceTypeRepositoryPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def _get_model(self, service_type_id: int) -> ServiceTypeModel | None:
        result = await self._db.execute(
            select(ServiceTypeModel).where(ServiceTypeModel.id == service_type_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self) -> list[ServiceType]:
        result = await self._db.execute(select(ServiceTypeModel).order_by(ServiceTypeModel.name))
        return [to_domain(model) for model in result.scalars().all()]

    async def get_by_id(self, service_type_id: int) -> ServiceType | None:
        model = await self._get_model(service_type_id)
        return to_domain(model) if model else None

    async def get_by_name(self, name: str) -> ServiceType | None:
        result = await self._db.execute(select(ServiceTypeModel).where(ServiceTypeModel.name == name))
        model = result.scalar_one_or_none()
        return to_domain(model) if model else None

    async def add(self, service_type: ServiceType) -> ServiceType:
        model = ServiceTypeModel(
            name=service_type.name,
            description=service_type.description,
            price=service_type.price,
            estimated_duration_minutes=service_type.estimated_duration_minutes,
        )
        self._db.add(model)
        await self._db.commit()
        await self._db.refresh(model)
        return to_domain(model)

    async def update(self, service_type: ServiceType) -> ServiceType:
        model = await self._get_model(service_type.id)
        model.name = service_type.name
        model.description = service_type.description
        model.price = service_type.price
        model.estimated_duration_minutes = service_type.estimated_duration_minutes
        await self._db.commit()
        await self._db.refresh(model)
        return to_domain(model)

    async def is_referenced(self, service_type_id: int) -> bool:
        query = select(exists().where(ServiceOrderItem.service_type_id == service_type_id))
        return bool((await self._db.execute(query)).scalar())

    async def delete(self, service_type_id: int) -> None:
        model = await self._get_model(service_type_id)
        if model is not None:
            await self._db.delete(model)
            try:
                await self._db.commit()
            except IntegrityError as exc:
                # Vínculo criado entre a verificação do caso de uso e a exclusão.
                await self._db.rollback()
                raise ConflictError(
                    f"Serviço {service_type_id} tem registros vinculados e não pode ser excluído"
                ) from exc
