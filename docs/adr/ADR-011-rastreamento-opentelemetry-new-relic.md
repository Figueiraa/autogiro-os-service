# ADR-011: Rastreamento distribuído com OpenTelemetry e New Relic

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira

## Contexto

O enunciado pede o uso das ferramentas de observabilidade da Fase 3 (New Relic e
Prometheus) e o **rastreamento dos fluxos distribuídos**. Na Fase 3 havia métricas por
serviço, mas nada ligava uma requisição a outra: com 3 serviços e um broker no meio, sem
rastreamento não dá para seguir uma OS.

## Decisão

A implementação é a mesma nos três serviços
([`app/infrastructure/observability/tracing.py`](../../app/infrastructure/observability/tracing.py)).

| Sinal | Como |
|---|---|
| **Traces** | **SDK do OpenTelemetry** em Python. Instrumentação automática de FastAPI (HTTP de entrada), httpx (HTTP de saída, inclusive Mercado Pago e o catálogo de peças), SQLAlchemy (OS e Billing) e pymongo (Execution). Exportação **OTLP/HTTP direta para o New Relic** (`https://otlp.nr-data.net`, caminho `/v1/traces`, gzip), autenticada com a license key no cabeçalho `api-key`. Recurso com `service.name`, `service.version` e `deployment.environment.name` |
| **Mensagens** | Spans manuais: `<exchange> send` (PRODUCER) no publicador do outbox e `<fila> process` (CONSUMER) no consumo, com os atributos `messaging.*` das convenções do OpenTelemetry |
| **Propagação** | Contexto W3C (`traceparent`) no cabeçalho HTTP e **nos cabeçalhos AMQP**. O contexto é capturado quando a mensagem entra no outbox (coluna ou campo `trace_context`), dentro da requisição ou do consumo que a originou, e não quando o publicador a envia. Assim o trace segue o fluxo de negócio, e não o laço de publicação |
| **Amostragem** | `ParentBased` com uma raiz que **não deixa um span CLIENT abrir trace**. Consultas ao banco e chamadas HTTP feitas pelos laços em segundo plano (outbox, reenvio de etapas paradas, monitor de pagamentos) não geram traces de uma linha; dentro de uma requisição ou mensagem, seguem a decisão do pai. `/health` e `/metrics` não geram span |
| **Correlação de negócio** | Atributos `autogiro.saga_id`, `autogiro.order_id`, `autogiro.message.type`, `autogiro.message.outcome` (`done`, `retry` ou `dead_letter`) e `autogiro.message.retry_count` nos spans de mensagem. No OS Service, o span de cada evento leva também `autogiro.saga.state`, `autogiro.saga.failure_reason`, `autogiro.saga.outcome` e `autogiro.order.status` |
| **Logs** | JSON em stdout com `service`, `trace.id` e `span.id` (nomes que o New Relic usa para ligar log e trace) e, durante o processamento de uma mensagem, `saga_id`, `order_id` e `message_type`. O agente do New Relic no cluster coleta os logs |
| **Métricas** | Prometheus como na Fase 3 (`GET /metrics` em cada serviço, raspado pelo agente do New Relic no cluster): métricas RED de HTTP e de negócio. Métricas do RabbitMQ pelo plugin `rabbitmq_prometheus`, com a profundidade de cada fila, inclusive das DLQs |
| **Painéis e alertas** | Terraform no `autogiro-infra-k8s` ([`newrelic-saga.tf`](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/terraform/newrelic-saga.tf)): painel **AutoGiro — Saga e rastreamento**, montado sobre os atributos dos spans, e o alerta **Mensagem da Saga na DLQ** (span com `autogiro.message.outcome = 'dead_letter'`) |

Sem `OTEL_EXPORTER_OTLP_ENDPOINT`, nada é exportado: a API do OpenTelemetry fica no modo
no-op e o serviço funciona igual (testes e execução local).

## Consequências

**Positivas**
- No New Relic, cada trecho automático do fluxo aparece como **um trace** atravessando
  OS Service → RabbitMQ → Execution → Billing, com o tempo de cada trecho. Por exemplo, a
  abertura da OS, a reserva das peças e a geração do orçamento formam um trace só.
- Mesma ferramenta e conta da Fase 3.
- OpenTelemetry é padrão aberto: trocar o destino dos dados é configuração, não código.

**Negativas**
- Uma Saga completa **não é um trace só**. Cada ação humana (aprovar, mecânico avançar a
  execução) é uma requisição nova, e o `PagamentoConfirmado` nasce no monitor de pagamentos,
  fora de qualquer requisição; comandos reenviados por etapa parada também abrem trace
  próprio. Os trechos de uma mesma OS são ligados pelos atributos `autogiro.saga_id` e
  `autogiro.order_id`, não por *links* entre spans.
- Mais dependências em cada serviço, e spans de mensagem escritos à mão (não há
  instrumentação automática do aio-pika em uso).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Agente APM Python do New Relic | Instrumentação de cliente RabbitMQ assíncrono (aio-pika) incerta; prende o código ao fornecedor |
| Só ID de correlação nos logs | Permite buscar, mas não mostra o fluxo nem o tempo de cada trecho |
| Jaeger ou Tempo no cluster | Ferramenta nova; o enunciado pede as ferramentas da Fase 3 |
