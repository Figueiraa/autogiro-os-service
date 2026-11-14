import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# Accepts old format (ABC-1234) and Mercosul (ABC1D23)
_PLATE_RE = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")

# Mesmos limites dos CHECKs e das colunas da migration 001: o que o banco recusaria
# vira 422 aqui, em vez de 500 no INSERT/UPDATE.
Year = Annotated[int, Field(ge=1900, le=2100, description="Ano do veículo (1900 a 2100)")]
BrandOrModel = Annotated[str, Field(max_length=100)]


class VehicleCreate(BaseModel):
    plate: str
    brand: BrandOrModel
    model: BrandOrModel
    year: Year
    client_id: int

    @field_validator("plate")
    @classmethod
    def validate_plate(cls, v: str) -> str:
        normalized = v.upper().replace("-", "").strip()
        old_format = re.match(r"^[A-Z]{3}[0-9]{4}$", normalized)
        mercosul = _PLATE_RE.match(normalized)
        if not old_format and not mercosul:
            raise ValueError("Placa inválida. Use o formato ABC1234 ou ABC1D23")
        return normalized


class VehicleUpdate(BaseModel):
    brand: BrandOrModel | None = None
    model: BrandOrModel | None = None
    year: Year | None = None


class VehicleResponse(BaseModel):
    id: int
    plate: str
    brand: str
    model: str
    year: int
    client_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
