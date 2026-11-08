"""Consulta ao catálogo de peças, que pertence ao Execution Service.

É a única chamada síncrona entre serviços: serve para avisar o atendente, na
hora da abertura, que uma peça não existe. Se o catálogo não responder, a OS é
aberta mesmo assim e a Saga trata a peça inexistente depois.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PartInfo:
    id: int
    name: str
    unit_price: float


class PartCatalogUnavailableError(Exception):
    """O catálogo não respondeu a tempo ou respondeu com erro."""


class PartCatalogPort(Protocol):
    async def find(self, part_id: int) -> PartInfo | None:
        """Devolve a peça, `None` se não existir, ou levanta `PartCatalogUnavailableError`."""
        ...
