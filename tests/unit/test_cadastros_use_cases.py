"""Casos de uso dos cadastros (clientes, veículos e catálogo de serviços) sobre SQLite."""

import pytest
from sqlalchemy import text

from app.application.dtos.client_dtos import CreateClientInput, UpdateClientInput
from app.application.dtos.service_type_dtos import CreateServiceTypeInput, UpdateServiceTypeInput
from app.application.dtos.vehicle_dtos import CreateVehicleInput, UpdateVehicleInput
from app.application.use_cases.client import (
    CreateClientUseCase,
    DeleteClientUseCase,
    GetClientUseCase,
    ListClientsUseCase,
    UpdateClientUseCase,
)
from app.application.use_cases.service_type import (
    CreateServiceTypeUseCase,
    DeleteServiceTypeUseCase,
    GetServiceTypeUseCase,
    ListServiceTypesUseCase,
    UpdateServiceTypeUseCase,
)
from app.application.use_cases.vehicle import (
    CreateVehicleUseCase,
    DeleteVehicleUseCase,
    GetVehicleUseCase,
    ListVehiclesByClientUseCase,
    ListVehiclesUseCase,
    UpdateVehicleUseCase,
)
from app.domain.entities.service_order import ServiceOrder, ServiceOrderItem
from app.domain.exceptions import ConflictError, NotFoundError
from app.infrastructure.persistence.repositories.client_repository import SqlAlchemyClientRepository
from app.infrastructure.persistence.repositories.service_order_repository import (
    SqlAlchemyServiceOrderRepository,
)
from app.infrastructure.persistence.repositories.service_type_repository import (
    SqlAlchemyServiceTypeRepository,
)
from app.infrastructure.persistence.repositories.vehicle_repository import SqlAlchemyVehicleRepository


@pytest.fixture
def clientes(db):
    return SqlAlchemyClientRepository(db)


@pytest.fixture
def veiculos(db):
    return SqlAlchemyVehicleRepository(db)


@pytest.fixture
def servicos(db):
    return SqlAlchemyServiceTypeRepository(db)


async def _maria(clientes):
    return await CreateClientUseCase(clientes).execute(
        CreateClientInput(name="Maria", cpf_cnpj="52998224725", email="maria@exemplo.com")
    )


class TestClientes:
    async def test_cria_lista_e_detalha(self, clientes):
        maria = await _maria(clientes)

        assert [c.id for c in await ListClientsUseCase(clientes).execute()] == [maria.id]
        assert (await GetClientUseCase(clientes).execute(maria.id)).name == "Maria"

    async def test_documento_duplicado_e_conflito(self, clientes):
        await _maria(clientes)
        with pytest.raises(ConflictError):
            await _maria(clientes)

    async def test_atualiza_todos_os_campos(self, clientes):
        maria = await _maria(clientes)

        atualizado = await UpdateClientUseCase(clientes).execute(
            maria.id, UpdateClientInput(name="Maria S.", phone="11999990000", email="m@x.com", address="Rua A, 1")
        )

        assert (atualizado.name, atualizado.phone, atualizado.email, atualizado.address) == (
            "Maria S.",
            "11999990000",
            "m@x.com",
            "Rua A, 1",
        )

    async def test_remove(self, clientes):
        maria = await _maria(clientes)
        await DeleteClientUseCase(clientes).execute(maria.id)
        assert await clientes.get_by_id(maria.id) is None

    async def test_cliente_com_veiculo_nao_e_removido(self, clientes, veiculos):
        maria = await _maria(clientes)
        await CreateVehicleUseCase(veiculos, clientes).execute(
            CreateVehicleInput(plate="ABC1D23", brand="Fiat", model="Uno", year=2020, client_id=maria.id)
        )

        with pytest.raises(ConflictError, match="veículos ou ordens de serviço vinculados"):
            await DeleteClientUseCase(clientes).execute(maria.id)
        assert await clientes.get_by_id(maria.id) is not None

    async def test_vinculo_recusado_pelo_banco_vira_conflito(self, clientes, veiculos):
        # Vínculo criado entre a checagem do caso de uso e a exclusão: o repositório
        # converte o erro de integridade em conflito, e a sessão continua utilizável.
        maria = await _maria(clientes)
        await CreateVehicleUseCase(veiculos, clientes).execute(
            CreateVehicleInput(plate="ABC1D23", brand="Fiat", model="Uno", year=2020, client_id=maria.id)
        )

        with pytest.raises(ConflictError):
            await clientes.delete(maria.id)
        assert await clientes.get_by_id(maria.id) is not None

    @pytest.mark.parametrize(
        "acao",
        [
            lambda repo: GetClientUseCase(repo).execute(99),
            lambda repo: UpdateClientUseCase(repo).execute(99, UpdateClientInput(name="X")),
            lambda repo: DeleteClientUseCase(repo).execute(99),
        ],
    )
    async def test_cliente_inexistente(self, clientes, acao):
        with pytest.raises(NotFoundError):
            await acao(clientes)


class TestVeiculos:
    async def _uno(self, clientes, veiculos):
        maria = await _maria(clientes)
        return maria, await CreateVehicleUseCase(veiculos, clientes).execute(
            CreateVehicleInput(plate="ABC1D23", brand="Fiat", model="Uno", year=2020, client_id=maria.id)
        )

    async def test_cria_lista_e_detalha(self, clientes, veiculos):
        maria, uno = await self._uno(clientes, veiculos)

        assert [v.id for v in await ListVehiclesUseCase(veiculos).execute()] == [uno.id]
        assert [v.id for v in await ListVehiclesByClientUseCase(veiculos, clientes).execute(maria.id)] == [uno.id]
        assert (await GetVehicleUseCase(veiculos).execute(uno.id)).plate == "ABC1D23"

    async def test_placa_duplicada_e_conflito(self, clientes, veiculos):
        maria, _ = await self._uno(clientes, veiculos)
        with pytest.raises(ConflictError):
            await CreateVehicleUseCase(veiculos, clientes).execute(
                CreateVehicleInput(plate="ABC1D23", brand="VW", model="Gol", year=2019, client_id=maria.id)
            )

    async def test_dono_inexistente(self, clientes, veiculos):
        with pytest.raises(NotFoundError):
            await CreateVehicleUseCase(veiculos, clientes).execute(
                CreateVehicleInput(plate="ABC1D23", brand="VW", model="Gol", year=2019, client_id=99)
            )
        with pytest.raises(NotFoundError):
            await ListVehiclesByClientUseCase(veiculos, clientes).execute(99)

    async def test_atualiza_e_remove(self, clientes, veiculos):
        _, uno = await self._uno(clientes, veiculos)

        atualizado = await UpdateVehicleUseCase(veiculos).execute(
            uno.id, UpdateVehicleInput(brand="Fiat", model="Uno Way", year=2021)
        )
        await DeleteVehicleUseCase(veiculos).execute(uno.id)

        assert (atualizado.model, atualizado.year) == ("Uno Way", 2021)
        assert await veiculos.get_by_id(uno.id) is None

    async def test_veiculo_com_ordem_de_servico_nao_e_removido(self, clientes, veiculos, servicos, db):
        maria, uno = await self._uno(clientes, veiculos)
        oleo = await CreateServiceTypeUseCase(servicos).execute(CreateServiceTypeInput(name="Óleo", price=149.9))
        item = ServiceOrderItem(service_type_id=oleo.id, name=oleo.name, quantity=1, unit_price=149.9)
        await SqlAlchemyServiceOrderRepository(db).add(
            ServiceOrder.open(number="OS1", vehicle_id=uno.id, client_id=maria.id, items=[item], parts=[])
        )

        with pytest.raises(ConflictError, match="ordens de serviço vinculadas"):
            await DeleteVehicleUseCase(veiculos).execute(uno.id)
        # Sem a checagem do caso de uso, o banco recusa e o repositório responde com conflito.
        with pytest.raises(ConflictError):
            await veiculos.delete(uno.id)
        assert await veiculos.get_by_id(uno.id) is not None

    @pytest.mark.parametrize(
        "acao",
        [
            lambda repo: GetVehicleUseCase(repo).execute(99),
            lambda repo: UpdateVehicleUseCase(repo).execute(99, UpdateVehicleInput(year=2020)),
            lambda repo: DeleteVehicleUseCase(repo).execute(99),
        ],
    )
    async def test_veiculo_inexistente(self, veiculos, acao):
        with pytest.raises(NotFoundError):
            await acao(veiculos)


class TestCatalogoDeServicos:
    async def _oleo(self, servicos):
        return await CreateServiceTypeUseCase(servicos).execute(CreateServiceTypeInput(name="Troca de óleo", price=149.9))

    async def test_cria_lista_e_detalha(self, servicos):
        oleo = await self._oleo(servicos)

        assert [s.id for s in await ListServiceTypesUseCase(servicos).execute()] == [oleo.id]
        assert (await GetServiceTypeUseCase(servicos).execute(oleo.id)).price == pytest.approx(149.9)

    async def test_nome_duplicado_e_conflito(self, servicos):
        await self._oleo(servicos)
        with pytest.raises(ConflictError):
            await self._oleo(servicos)

    async def test_renomear_para_um_nome_existente_e_conflito(self, servicos):
        await self._oleo(servicos)
        freio = await CreateServiceTypeUseCase(servicos).execute(CreateServiceTypeInput(name="Freio", price=320))

        with pytest.raises(ConflictError):
            await UpdateServiceTypeUseCase(servicos).execute(freio.id, UpdateServiceTypeInput(name="Troca de óleo"))

    async def test_atualiza_e_remove(self, servicos):
        oleo = await self._oleo(servicos)

        atualizado = await UpdateServiceTypeUseCase(servicos).execute(
            oleo.id,
            UpdateServiceTypeInput(
                name="Troca de óleo sintético", description="5W30", price=189.9, estimated_duration_minutes=50
            ),
        )
        await DeleteServiceTypeUseCase(servicos).execute(oleo.id)

        assert (atualizado.name, atualizado.description, atualizado.estimated_duration_minutes) == (
            "Troca de óleo sintético",
            "5W30",
            50,
        )
        assert await servicos.get_by_id(oleo.id) is None

    async def test_vinculo_recusado_pelo_banco_vira_conflito(self, clientes, veiculos, servicos, db):
        oleo = await self._oleo(servicos)
        maria = await _maria(clientes)
        uno = await CreateVehicleUseCase(veiculos, clientes).execute(
            CreateVehicleInput(plate="ABC1D23", brand="Fiat", model="Uno", year=2020, client_id=maria.id)
        )
        item = ServiceOrderItem(service_type_id=oleo.id, name=oleo.name, quantity=1, unit_price=149.9)
        await SqlAlchemyServiceOrderRepository(db).add(
            ServiceOrder.open(number="OS1", vehicle_id=uno.id, client_id=maria.id, items=[item], parts=[])
        )
        # O SQLite só confere chave estrangeira com o PRAGMA ligado (o PostgreSQL sempre confere).
        await db.execute(text("PRAGMA foreign_keys=ON"))

        with pytest.raises(ConflictError):
            await servicos.delete(oleo.id)
        with pytest.raises(ConflictError, match="já foi usado em ordens de serviço"):
            await DeleteServiceTypeUseCase(servicos).execute(oleo.id)

    @pytest.mark.parametrize(
        "acao",
        [
            lambda repo: GetServiceTypeUseCase(repo).execute(99),
            lambda repo: UpdateServiceTypeUseCase(repo).execute(99, UpdateServiceTypeInput(price=1)),
            lambda repo: DeleteServiceTypeUseCase(repo).execute(99),
        ],
    )
    async def test_servico_inexistente(self, servicos, acao):
        with pytest.raises(NotFoundError):
            await acao(servicos)
