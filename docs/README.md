# Documentação do AutoGiro

Arquitetura da plataforma (os três microsserviços e a Saga da ordem de serviço). Fica neste
repositório porque o OS Service é o orquestrador da Saga.

## Arquitetura

| Documento | Conteúdo |
|---|---|
| [Visão geral](arquitetura/visao-geral.md) | Diagrama geral, os três serviços e as estratégias de comunicação |
| [Posse dos dados](arquitetura/posse-dos-dados.md) | Dono de cada dado, modelo de cada banco e o que é cópia |
| [Máquinas de estado](arquitetura/maquina-de-estados.md) | OS, Saga, orçamento, pagamento, reserva e execução |
| [Saga](arquitetura/saga.md) | Etapas, compensações, idempotência, isolamento, falhas e sequências |
| [Mensageria](arquitetura/mensageria.md) | Envelope, topologia no RabbitMQ, catálogo das 23 mensagens, retry e DLQ |
| [APIs e gateway](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/docs/apis-e-gateway.md) | Endpoints de cada serviço e rotas no Kong (no `autogiro-infra-k8s`) |

## Decisões (ADRs)

| ADR | Decisão |
|---|---|
| [005](adr/ADR-005-tres-microsservicos.md) | Divisão em três microsserviços |
| [006](adr/ADR-006-banco-por-servico.md) | Um banco por serviço: PostgreSQL × 2 e MongoDB |
| [007](adr/ADR-007-rest-na-borda-rabbitmq-entre-servicos.md) | REST na borda, RabbitMQ entre serviços |
| [008](adr/ADR-008-saga-orquestrada.md) | Saga orquestrada no OS Service |
| [009](adr/ADR-009-confiabilidade-das-mensagens.md) | Outbox nos três serviços, idempotência, reenvio, retry e DLQ |
| [010](https://github.com/Figueiraa/autogiro-billing-service/blob/main/docs/adr/ADR-010-mercado-pago-checkout-pro.md) | Mercado Pago Checkout Pro com confirmação por consulta (no `autogiro-billing-service`) |
| [011](adr/ADR-011-rastreamento-opentelemetry-new-relic.md) | Rastreamento com OpenTelemetry, exportado para o New Relic |
| [012](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/docs/adr/ADR-012-kubernetes-no-eks.md) | Kubernetes no EKS, ligado só quando necessário (no `autogiro-infra-k8s`) |
| [013](adr/ADR-013-lambda-no-contexto-de-atendimento.md) | Lambda de autenticação no contexto de Atendimento |

## Outros materiais

- [Coleção Postman do fluxo completo](postman/autogiro-fluxo-completo.postman_collection.json):
  abertura, Saga, orçamento, pagamento, execução e entrega nos três serviços.
