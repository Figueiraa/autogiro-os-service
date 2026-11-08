import os

# Definidos antes de importar a aplicação: o JWT_SECRET é obrigatório e não tem
# valor padrão no código, e o banco dos testes é SQLite em memória.
os.environ.setdefault("JWT_SECRET", "segredo-de-teste-com-mais-de-32-bytes-de-tamanho")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.application.ports.part_catalog import PartCatalogUnavailableError, PartInfo
from app.domain.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.infrastructure.database import Base, get_db
from app.infrastructure.security.jwt_tokens import Principal, issue_token
from app.interfaces.http.controllers.service_order_controller import get_part_catalog
from app.interfaces.http.dependencies import get_current_principal
from app.main import API_V1, create_app

# CPF válido pelos dígitos verificadores, usado como o cliente autenticado.
AUTH_CPF = "11144477735"


def pytest_collection_modifyitems(items):
    """Marca cada teste conforme a pasta, para rodar `pytest -m unit` ou `-m integration`."""
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)


def _rotas_de_teste() -> APIRouter:
    """Rotas que existem só nos testes, para exercitar a infraestrutura comum
    (autenticação, métricas por template de rota, tratamento de exceções)."""
    router = APIRouter(prefix="/_test")

    @router.get("/protected")
    async def protected(principal: Principal = Depends(get_current_principal)):
        return {"subject": principal.subject, "client_id": principal.client_id}

    @router.get("/items/{item_id}")
    async def item(item_id: int):
        return {"id": item_id}

    @router.get("/not-found")
    async def not_found():
        raise NotFoundError("Item", 42)

    @router.get("/conflict")
    async def conflict():
        raise ConflictError("Item duplicado")

    @router.get("/business-rule")
    async def business_rule():
        raise BusinessRuleError("Transição inválida")

    @router.get("/boom")
    async def boom():
        raise RuntimeError("falha inesperada")

    return router


class FakePartCatalog:
    """Catálogo de peças do Execution Service, em memória."""

    def __init__(self, parts: dict[int, PartInfo] | None = None, available: bool = True):
        self.parts = parts if parts is not None else {}
        self.available = available
        self.lookups: list[int] = []

    async def find(self, part_id: int) -> PartInfo | None:
        self.lookups.append(part_id)
        if not self.available:
            raise PartCatalogUnavailableError("fora do ar")
        return self.parts.get(part_id)


@pytest.fixture
def part_catalog() -> FakePartCatalog:
    return FakePartCatalog(
        {
            3: PartInfo(id=3, name="Filtro de óleo", unit_price=32.5),
            4: PartInfo(id=4, name="Pastilha de freio", unit_price=210.0),
        }
    )


@pytest_asyncio.fixture
async def session_factory():
    """Banco em memória compartilhado entre sessões (StaticPool: uma conexão só)."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(session_factory):
    async with session_factory() as session:
        yield session


@pytest.fixture
def app(db: AsyncSession, part_catalog: FakePartCatalog):
    application = create_app()
    application.include_router(_rotas_de_teste(), prefix=API_V1)

    # Os testes de sondas registram as próprias verificações; a do banco real tem teste próprio.
    application.state.readiness_checks = {}
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[get_part_catalog] = lambda: part_catalog
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def token() -> str:
    return issue_token(AUTH_CPF, client_id=1, name="Cliente de Teste")


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_client(app, auth_headers):
    """Cliente HTTP autenticado com um JWT no formato emitido pelo `autogiro-auth`."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test", headers=auth_headers) as c:
        yield c
