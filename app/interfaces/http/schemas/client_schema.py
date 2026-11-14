import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# Tamanhos das colunas de `clients` (migration 001): texto maior vira 422 aqui, em
# vez de 500 no INSERT/UPDATE.
Name = Annotated[str, Field(max_length=255)]
Phone = Annotated[str, Field(max_length=20)]
Email = Annotated[str, Field(max_length=255)]
Address = Annotated[str, Field(max_length=500)]


def _clean_document(value: str) -> str:
    return re.sub(r"\D", "", value)


def _validate_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for i in range(9, 11):
        total = sum(int(digits[j]) * (i + 1 - j) for j in range(i))
        check = (total * 10 % 11) % 10
        if check != int(digits[i]):
            return False
    return True


def _validate_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6] + weights1
    for weights, pos in [(weights1, 12), (weights2, 13)]:
        total = sum(int(digits[i]) * weights[i] for i in range(pos))
        remainder = total % 11
        check = 0 if remainder < 2 else 11 - remainder
        if check != int(digits[pos]):
            return False
    return True


class ClientCreate(BaseModel):
    name: Name
    cpf_cnpj: str
    phone: Phone | None = None
    email: Email | None = None
    address: Address | None = None

    @field_validator("cpf_cnpj")
    @classmethod
    def validate_cpf_cnpj(cls, v: str) -> str:
        digits = _clean_document(v)
        if not (_validate_cpf(digits) or _validate_cnpj(digits)):
            raise ValueError("CPF ou CNPJ inválido")
        return digits


class ClientUpdate(BaseModel):
    name: Name | None = None
    phone: Phone | None = None
    email: Email | None = None
    address: Address | None = None


class ClientResponse(BaseModel):
    id: int
    name: str
    cpf_cnpj: str
    phone: str | None
    email: str | None
    address: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
