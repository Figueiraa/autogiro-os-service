"""Adaptadores do catálogo de peças do Execution Service."""

import httpx

from app.application.ports.part_catalog import PartCatalogPort, PartCatalogUnavailableError, PartInfo


class HttpPartCatalog(PartCatalogPort):
    """Consulta `GET /api/v1/parts/{id}` no Execution Service.

    Chama o serviço pelo DNS do Kubernetes, sem passar pelo Kong, e repassa o
    token de quem abriu a OS: o Execution Service também valida quem pede.
    """

    def __init__(
        self, base_url: str, token: str, timeout_seconds: float, client: httpx.AsyncClient | None = None
    ):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_seconds
        self._client = client

    async def find(self, part_id: int) -> PartInfo | None:
        url = f"{self._base_url}/api/v1/parts/{part_id}"
        try:
            if self._client is not None:
                response = await self._client.get(url, headers=self._headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, headers=self._headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise PartCatalogUnavailableError(str(exc)) from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code != httpx.codes.OK:
            raise PartCatalogUnavailableError(f"Execution Service respondeu {response.status_code}")

        body = response.json()
        return PartInfo(id=body["id"], name=body["name"], unit_price=float(body["unit_price"]))


class DisabledPartCatalog(PartCatalogPort):
    """Usado quando `EXECUTION_SERVICE_URL` não está configurada: a validação fica com a Saga."""

    async def find(self, part_id: int) -> PartInfo | None:
        raise PartCatalogUnavailableError("EXECUTION_SERVICE_URL não configurada")
