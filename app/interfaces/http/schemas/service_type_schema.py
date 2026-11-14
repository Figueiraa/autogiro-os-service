from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

# Mesmos limites dos CHECKs e das colunas da migration 001: o que o banco recusaria
# vira 422 aqui, em vez de 500 no INSERT/UPDATE.
Name = Annotated[str, Field(max_length=255)]
# NUMERIC(10, 2): até 99.999.999,99.
Price = Annotated[float, Field(ge=0, lt=100_000_000)]
# INTEGER do PostgreSQL.
Duration = Annotated[int, Field(gt=0, le=2_147_483_647, description="Duração estimada, em minutos")]


class ServiceTypeCreate(BaseModel):
    name: Name
    description: str | None = None
    price: Price
    estimated_duration_minutes: Duration = 60


class ServiceTypeUpdate(BaseModel):
    name: Name | None = None
    description: str | None = None
    price: Price | None = None
    estimated_duration_minutes: Duration | None = None


class ServiceTypeResponse(BaseModel):
    id: int
    name: str
    description: str | None
    price: float
    estimated_duration_minutes: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
