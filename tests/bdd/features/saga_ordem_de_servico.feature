# language: pt
Funcionalidade: Saga da ordem de serviço
  Como oficina, quero que a abertura, o orçamento, o pagamento e a execução de uma
  ordem de serviço avancem juntos entre os serviços, e que qualquer falha desfaça
  só o que já tinha sido feito.

  Contexto:
    Dado a cliente "Maria" com um veículo cadastrado
    E o serviço "Troca de óleo" de R$ 149,90 no catálogo

  Cenário: Caminho feliz, da abertura à entrega do veículo
    Quando o atendente abre uma OS com o serviço e 2 unidades da peça 3
    Então o Execution recebe o comando "ReservarPecas"
    Quando o Execution confirma a reserva da peça 3 a R$ 32,50
    Então o Billing recebe o comando "GerarOrcamento" com o total de R$ 214,90
    Quando o Billing gera o orçamento de R$ 214,90
    Então a OS fica "AGUARDANDO_APROVACAO" com orçamento de R$ 214,90
    Quando a cliente aprova o orçamento
    Então a OS fica "AGUARDANDO_PAGAMENTO" com o link de pagamento
    Quando o Mercado Pago confirma o pagamento
    Então o Execution recebe o comando "EnfileirarExecucao"
    Quando a OS entra na fila da oficina
    E o mecânico faz o diagnóstico, o reparo e finaliza
    Então a OS fica "FINALIZADA"
    E a Saga termina "CONCLUIDA"
    Quando o atendente entrega o veículo
    Então a OS fica "ENTREGUE"
    E o histórico registra cada etapa com o evento que a causou

  Cenário: Peça indisponível cancela a OS sem nada a desfazer
    Quando o atendente abre uma OS com o serviço e 2 unidades da peça 3
    E o Execution responde que a peça 3 está sem estoque
    Então a OS fica "CANCELADA" pelo motivo "PECAS_INDISPONIVEIS"
    E nenhum comando de compensação é enviado
    E a Saga termina "COMPENSADA"

  Cenário: Orçamento recusado libera as peças e cancela a cobrança
    Dado uma OS aguardando a aprovação do orçamento
    Quando a cliente recusa o orçamento
    Então a OS fica "EM_CANCELAMENTO" pelo motivo "ORCAMENTO_RECUSADO"
    E são enviadas as compensações "LiberarPecas" e "CancelarCobranca"
    Quando o Execution confirma a liberação das peças
    E o Billing confirma o cancelamento da cobrança
    Então a OS fica "CANCELADA" pelo motivo "ORCAMENTO_RECUSADO"
    E a Saga termina "COMPENSADA"

  Cenário: Pagamento recusado desfaz a reserva e a cobrança
    Dado uma OS aguardando o pagamento
    Quando o Mercado Pago recusa o pagamento
    Então a OS fica "EM_CANCELAMENTO" pelo motivo "PAGAMENTO_RECUSADO"
    E são enviadas as compensações "LiberarPecas" e "CancelarCobranca"
    Quando o Execution confirma a liberação das peças
    E o Billing confirma o cancelamento da cobrança
    Então a OS fica "CANCELADA" pelo motivo "PAGAMENTO_RECUSADO"
    E a Saga termina "COMPENSADA"

  Cenário: Oficina lotada depois do pagamento estorna o valor pago
    Dado uma OS paga aguardando a fila da oficina
    Quando o Execution recusa a OS porque a fila está cheia
    Então a OS fica "EM_CANCELAMENTO" pelo motivo "EXECUCAO_RECUSADA"
    E são enviadas as compensações "LiberarPecas" e "CancelarCobranca"
    Quando o Execution confirma a liberação das peças
    E o Billing confirma o cancelamento da cobrança com estorno
    Então a OS fica "CANCELADA" pelo motivo "EXECUCAO_RECUSADA"
    E a Saga termina "COMPENSADA"
