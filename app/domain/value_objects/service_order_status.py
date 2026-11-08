"""Máquina de estados da Ordem de Serviço.

A OS avança por eventos da Saga (orçamento, pagamento, execução) e por uma única
ação manual, a entrega. Qualquer etapa antes da finalização pode falhar: a OS
passa por `EM_CANCELAMENTO` enquanto as compensações rodam e termina `CANCELADA`.
"""

import enum


class ServiceOrderStatus(str, enum.Enum):
    RECEBIDA = "RECEBIDA"
    AGUARDANDO_APROVACAO = "AGUARDANDO_APROVACAO"
    AGUARDANDO_PAGAMENTO = "AGUARDANDO_PAGAMENTO"
    PAGA = "PAGA"
    NA_FILA = "NA_FILA"
    EM_DIAGNOSTICO = "EM_DIAGNOSTICO"
    EM_EXECUCAO = "EM_EXECUCAO"
    FINALIZADA = "FINALIZADA"
    ENTREGUE = "ENTREGUE"
    EM_CANCELAMENTO = "EM_CANCELAMENTO"
    CANCELADA = "CANCELADA"


class CancelReason(str, enum.Enum):
    """Por que a Saga desfez a OS (registrado em `cancel_reason`)."""

    PECAS_INDISPONIVEIS = "PECAS_INDISPONIVEIS"
    ORCAMENTO_FALHOU = "ORCAMENTO_FALHOU"
    ORCAMENTO_RECUSADO = "ORCAMENTO_RECUSADO"
    APROVACAO_EXPIRADA = "APROVACAO_EXPIRADA"
    PAGAMENTO_RECUSADO = "PAGAMENTO_RECUSADO"
    PAGAMENTO_EXPIRADO = "PAGAMENTO_EXPIRADO"
    EXECUCAO_RECUSADA = "EXECUCAO_RECUSADA"
    EXECUCAO_CANCELADA = "EXECUCAO_CANCELADA"


_S = ServiceOrderStatus

# Para cada status, os status para os quais é permitido ir. Toda etapa antes da
# finalização pode falhar e entrar em cancelamento; depois de finalizada, a OS só
# pode ser entregue (não há mais nada a compensar).
VALID_TRANSITIONS: dict[ServiceOrderStatus, list[ServiceOrderStatus]] = {
    _S.RECEBIDA: [_S.AGUARDANDO_APROVACAO, _S.EM_CANCELAMENTO],
    _S.AGUARDANDO_APROVACAO: [_S.AGUARDANDO_PAGAMENTO, _S.EM_CANCELAMENTO],
    _S.AGUARDANDO_PAGAMENTO: [_S.PAGA, _S.EM_CANCELAMENTO],
    _S.PAGA: [_S.NA_FILA, _S.EM_CANCELAMENTO],
    _S.NA_FILA: [_S.EM_DIAGNOSTICO, _S.EM_CANCELAMENTO],
    _S.EM_DIAGNOSTICO: [_S.EM_EXECUCAO, _S.EM_CANCELAMENTO],
    _S.EM_EXECUCAO: [_S.FINALIZADA, _S.EM_CANCELAMENTO],
    _S.FINALIZADA: [_S.ENTREGUE],
    _S.ENTREGUE: [],
    _S.EM_CANCELAMENTO: [_S.CANCELADA],
    _S.CANCELADA: [],
}

# Status que saem da listagem de trabalho ativo da oficina.
HIDDEN_FROM_ACTIVE_LIST: list[ServiceOrderStatus] = [_S.FINALIZADA, _S.ENTREGUE, _S.CANCELADA]

# Ordem de exibição da listagem: o que exige ação da oficina vem primeiro.
LIST_PRIORITY: dict[ServiceOrderStatus, int] = {
    _S.EM_EXECUCAO: 1,
    _S.AGUARDANDO_APROVACAO: 2,
    _S.EM_DIAGNOSTICO: 3,
    _S.RECEBIDA: 4,
    _S.AGUARDANDO_PAGAMENTO: 5,
    _S.PAGA: 6,
    _S.NA_FILA: 7,
    _S.EM_CANCELAMENTO: 8,
}
