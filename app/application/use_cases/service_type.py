from app.application.dtos.service_type_dtos import CreateServiceTypeInput, UpdateServiceTypeInput
from app.application.ports.service_type_repository import ServiceTypeRepositoryPort
from app.domain.entities.service_type import ServiceType
from app.domain.exceptions import ConflictError, NotFoundError


class ListServiceTypesUseCase:
    def __init__(self, repository: ServiceTypeRepositoryPort):
        self._repo = repository

    async def execute(self) -> list[ServiceType]:
        return await self._repo.get_all()


class GetServiceTypeUseCase:
    def __init__(self, repository: ServiceTypeRepositoryPort):
        self._repo = repository

    async def execute(self, service_type_id: int) -> ServiceType:
        service_type = await self._repo.get_by_id(service_type_id)
        if not service_type:
            raise NotFoundError("Serviço", service_type_id)
        return service_type


class CreateServiceTypeUseCase:
    def __init__(self, repository: ServiceTypeRepositoryPort):
        self._repo = repository

    async def execute(self, data: CreateServiceTypeInput) -> ServiceType:
        if await self._repo.get_by_name(data.name):
            raise ConflictError(f"Serviço '{data.name}' já cadastrado")
        service_type = ServiceType(
            name=data.name,
            price=data.price,
            estimated_duration_minutes=data.estimated_duration_minutes,
            description=data.description,
        )
        return await self._repo.add(service_type)


class UpdateServiceTypeUseCase:
    def __init__(self, repository: ServiceTypeRepositoryPort):
        self._repo = repository

    async def execute(self, service_type_id: int, data: UpdateServiceTypeInput) -> ServiceType:
        service_type = await self._repo.get_by_id(service_type_id)
        if not service_type:
            raise NotFoundError("Serviço", service_type_id)
        if data.name and data.name != service_type.name:
            if await self._repo.get_by_name(data.name):
                raise ConflictError(f"Serviço '{data.name}' já cadastrado")
        if data.name is not None:
            service_type.name = data.name
        if data.description is not None:
            service_type.description = data.description
        if data.price is not None:
            service_type.price = data.price
        if data.estimated_duration_minutes is not None:
            service_type.estimated_duration_minutes = data.estimated_duration_minutes
        return await self._repo.update(service_type)


class DeleteServiceTypeUseCase:
    def __init__(self, repository: ServiceTypeRepositoryPort):
        self._repo = repository

    async def execute(self, service_type_id: int) -> None:
        service_type = await self._repo.get_by_id(service_type_id)
        if not service_type:
            raise NotFoundError("Serviço", service_type_id)
        # O banco recusa a exclusão (RESTRICT); checar antes dá uma mensagem clara.
        if await self._repo.is_referenced(service_type_id):
            raise ConflictError(
                f"Serviço {service_type_id} já foi usado em ordens de serviço e não pode ser excluído"
            )
        await self._repo.delete(service_type_id)
