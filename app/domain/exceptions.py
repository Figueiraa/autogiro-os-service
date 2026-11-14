"""Exceções de domínio. A camada HTTP as converte em códigos de status."""


class DomainError(Exception):
    """Base de todas as exceções de negócio do serviço."""


class NotFoundError(DomainError):
    def __init__(self, entity: str, identifier: object):
        super().__init__(f"{entity} {identifier} não encontrado(a)")
        self.entity = entity
        self.identifier = identifier


class ConflictError(DomainError):
    """Operação conflita com o estado atual (por exemplo, duplicidade)."""


class BusinessRuleError(DomainError):
    """Regra de negócio violada (por exemplo, transição de status inválida)."""


class InvalidStatusTransitionError(BusinessRuleError):
    """A máquina de estados da OS não permite a transição pedida."""

    def __init__(self, current: str, target: str):
        super().__init__(f"Transição de status inválida: '{current}' → '{target}'")
        self.current = current
        self.target = target


class OutOfOrderEventError(DomainError):
    """Evento da Saga chegou antes do evento que o precede.

    Não é erro de negócio nem repetição: o evento deve voltar mais tarde (fila de
    retry), quando o anterior já tiver sido processado.
    """

    def __init__(self, event_type: str, detail: str):
        super().__init__(f"{event_type} chegou fora de ordem: {detail}")
        self.event_type = event_type
