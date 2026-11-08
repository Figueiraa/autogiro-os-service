"""Validação de CPF (Cadastro de Pessoas Físicas).

Regra de domínio: a autenticação da Fase 3 identifica o cliente pelo CPF, então a
validade do documento é decidida aqui e não na borda HTTP.

Mesma implementação da Lambda `autogiro-auth` (`src/cpf.py`), deliberadamente
duplicada: os dois artefatos são deployados de forma independente e uma biblioteca
compartilhada acoplaria seus ciclos de release. A regra é estável — dígitos
verificadores por módulo 11 não mudam.
"""

import re

CPF_LENGTH = 11

# CPFs com todos os dígitos iguais passam no cálculo do dígito verificador,
# mas são inválidos por convenção da Receita Federal.
_REPEATED = {str(digit) * CPF_LENGTH for digit in range(10)}


def normalize(cpf: str) -> str:
    """Remove máscara (pontos, hífen e espaços), preservando apenas dígitos."""
    return re.sub(r"\D", "", cpf or "")


def _check_digit(digits: str, weight: int) -> str:
    """Calcula um dígito verificador pelo módulo 11."""
    total = sum(int(d) * w for d, w in zip(digits, range(weight, 1, -1), strict=True))
    remainder = (total * 10) % 11
    return "0" if remainder == 10 else str(remainder)


def is_valid(cpf: str) -> bool:
    """Valida o CPF pelos dois dígitos verificadores.

    Aceita com ou sem máscara. Rejeita sequências repetidas (111.111.111-11).
    """
    digits = normalize(cpf)

    if len(digits) != CPF_LENGTH or digits in _REPEATED:
        return False

    first = _check_digit(digits[:9], 10)
    second = _check_digit(digits[:9] + first, 11)

    return digits[9:] == first + second


def format_masked(cpf: str) -> str:
    """Formata como 000.000.000-00. Retorna a entrada normalizada se inválida."""
    digits = normalize(cpf)
    if len(digits) != CPF_LENGTH:
        return digits
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
