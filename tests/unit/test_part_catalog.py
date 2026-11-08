"""Consulta síncrona ao catálogo de peças do Execution Service."""

import httpx
import pytest

from app.application.ports.part_catalog import PartCatalogUnavailableError, PartInfo
from app.infrastructure.config import settings
from app.infrastructure.gateways.part_catalog import DisabledPartCatalog, HttpPartCatalog
from app.infrastructure.security.jwt_tokens import Principal
from app.interfaces.http.controllers.service_order_controller import get_part_catalog


def _catalogo(handler) -> HttpPartCatalog:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpPartCatalog("http://execution-service/", "token-do-atendente", 2.0, client=client)


class TestHttpPartCatalog:
    async def test_peca_encontrada(self):
        pedidos = []

        def handler(request: httpx.Request) -> httpx.Response:
            pedidos.append(request)
            return httpx.Response(200, json={"id": 3, "name": "Filtro de óleo", "unit_price": "32.50"})

        peca = await _catalogo(handler).find(3)

        assert peca == PartInfo(id=3, name="Filtro de óleo", unit_price=32.5)
        assert str(pedidos[0].url) == "http://execution-service/api/v1/parts/3"
        assert pedidos[0].headers["Authorization"] == "Bearer token-do-atendente"

    async def test_peca_inexistente_devolve_none(self):
        assert await _catalogo(lambda _: httpx.Response(404)).find(99) is None

    async def test_erro_do_servico_vira_indisponivel(self):
        with pytest.raises(PartCatalogUnavailableError, match="503"):
            await _catalogo(lambda _: httpx.Response(503)).find(3)

    async def test_timeout_vira_indisponivel(self):
        def handler(request):
            raise httpx.ReadTimeout("demorou", request=request)

        with pytest.raises(PartCatalogUnavailableError):
            await _catalogo(handler).find(3)

    async def test_sem_cliente_injetado_abre_conexao_propria(self):
        # Porta 9 (discard) recusa a conexão na hora: o erro de rede vira indisponível.
        catalogo = HttpPartCatalog("http://127.0.0.1:9", "t", 0.5)
        with pytest.raises(PartCatalogUnavailableError):
            await catalogo.find(3)


class TestDisabledPartCatalog:
    async def test_sempre_indisponivel(self):
        with pytest.raises(PartCatalogUnavailableError, match="EXECUTION_SERVICE_URL"):
            await DisabledPartCatalog().find(3)


class TestEscolhaDoCatalogo:
    PRINCIPAL = Principal(subject="11144477735", client_id=1, name="Maria", token="abc")

    def test_sem_url_configurada_usa_o_catalogo_desligado(self, monkeypatch):
        monkeypatch.setattr(settings, "EXECUTION_SERVICE_URL", None)
        assert isinstance(get_part_catalog(self.PRINCIPAL), DisabledPartCatalog)

    def test_com_url_chama_o_execution_service_com_o_token_do_usuario(self, monkeypatch):
        monkeypatch.setattr(settings, "EXECUTION_SERVICE_URL", "http://execution-service")

        catalogo = get_part_catalog(self.PRINCIPAL)

        assert isinstance(catalogo, HttpPartCatalog)
        assert catalogo._headers == {"Authorization": "Bearer abc"}
