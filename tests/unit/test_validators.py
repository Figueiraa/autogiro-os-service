import pytest
from pydantic import ValidationError

from app.interfaces.http.schemas.client_schema import ClientCreate, ClientUpdate
from app.interfaces.http.schemas.service_order_schema import ServiceOrderItemInput, ServiceOrderPartInput
from app.interfaces.http.schemas.service_type_schema import ServiceTypeCreate, ServiceTypeUpdate
from app.interfaces.http.schemas.vehicle_schema import VehicleCreate, VehicleUpdate


class TestCpfCnpjValidation:
    def test_valid_cpf(self):
        data = ClientCreate(name="João", cpf_cnpj="529.982.247-25")
        assert data.cpf_cnpj == "52998224725"

    def test_valid_cnpj(self):
        data = ClientCreate(name="Empresa", cpf_cnpj="11.222.333/0001-81")
        assert data.cpf_cnpj == "11222333000181"

    def test_invalid_cpf_raises(self):
        with pytest.raises(ValidationError):
            ClientCreate(name="João", cpf_cnpj="111.111.111-11")

    def test_invalid_cnpj_raises(self):
        with pytest.raises(ValidationError):
            ClientCreate(name="Empresa", cpf_cnpj="00.000.000/0000-00")

    def test_all_same_digits_cpf_raises(self):
        with pytest.raises(ValidationError):
            ClientCreate(name="João", cpf_cnpj="000.000.000-00")


class TestPlateValidation:
    def test_valid_old_format(self):
        data = VehicleCreate(plate="ABC-1234", brand="VW", model="Gol", year=2010, client_id=1)
        assert data.plate == "ABC1234"

    def test_valid_mercosul(self):
        data = VehicleCreate(plate="ABC1D23", brand="VW", model="Gol", year=2020, client_id=1)
        assert data.plate == "ABC1D23"

    def test_invalid_plate_raises(self):
        with pytest.raises(ValidationError):
            VehicleCreate(plate="INVALID", brand="VW", model="Gol", year=2020, client_id=1)

    def test_invalid_year_raises(self):
        with pytest.raises(ValidationError):
            VehicleCreate(plate="ABC1234", brand="VW", model="Gol", year=1800, client_id=1)


class TestLimitesDoBanco:
    """O que os CHECKs e os tamanhos de coluna da migration 001 recusariam vira 422, não 500."""

    @pytest.mark.parametrize("ano", [1899, 2101])
    def test_ano_fora_do_check_e_recusado_na_criacao_e_na_atualizacao(self, ano):
        with pytest.raises(ValidationError):
            VehicleCreate(plate="ABC1234", brand="VW", model="Gol", year=ano, client_id=1)
        with pytest.raises(ValidationError):
            VehicleUpdate(year=ano)

    def test_ano_no_limite_e_aceito(self):
        assert VehicleCreate(plate="ABC1234", brand="VW", model="Gol", year=1900, client_id=1).year == 1900
        assert VehicleUpdate(year=2100).year == 2100

    def test_marca_maior_que_a_coluna_e_recusada(self):
        with pytest.raises(ValidationError):
            VehicleUpdate(brand="x" * 101)

    @pytest.mark.parametrize(
        "campos",
        [{"price": -0.01}, {"price": 100_000_000}, {"estimated_duration_minutes": 0}, {"name": "x" * 256}],
    )
    def test_servico_fora_dos_limites_e_recusado(self, campos):
        with pytest.raises(ValidationError):
            ServiceTypeCreate(**({"name": "Revisão", "price": 100.0} | campos))
        with pytest.raises(ValidationError):
            ServiceTypeUpdate(**campos)

    def test_servico_gratuito_e_aceito(self):
        assert ServiceTypeCreate(name="Avaliação", price=0).price == 0

    @pytest.mark.parametrize(
        "campos", [{"phone": "1" * 21}, {"email": "a" * 256}, {"address": "r" * 501}, {"name": "n" * 256}]
    )
    def test_cliente_maior_que_as_colunas_e_recusado(self, campos):
        with pytest.raises(ValidationError):
            ClientCreate(**({"name": "João", "cpf_cnpj": "529.982.247-25"} | campos))
        with pytest.raises(ValidationError):
            ClientUpdate(**campos)

    def test_telefone_no_tamanho_da_coluna_e_aceito(self):
        assert ClientUpdate(phone="1" * 20).phone == "1" * 20

    @pytest.mark.parametrize("quantidade", [0, 1001])
    def test_quantidade_fora_dos_limites_e_recusada(self, quantidade):
        with pytest.raises(ValidationError):
            ServiceOrderItemInput(service_type_id=1, quantity=quantidade)
        with pytest.raises(ValidationError):
            ServiceOrderPartInput(part_id=1, quantity=quantidade)
