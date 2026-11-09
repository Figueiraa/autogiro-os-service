# ADR-007: REST na borda, mensageria com RabbitMQ entre serviços

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira
- **Substitui:** a decisão anterior de comunicação só por REST síncrono

## Contexto

Na Fase 3 a comunicação era só REST síncrono, porque havia um serviço só. A própria decisão
registrava o custo: acoplamento temporal, sem absorção de picos, sem retentativa automática.
Agora o fluxo da OS atravessa 3 serviços e inclui esperas de horas (aprovação, pagamento,
fila da oficina). Uma cadeia síncrona teria a disponibilidade do **elo mais fraco**, e o
enunciado exige resiliência e mensageria assíncrona.

## Decisão

| Comunicação | Tecnologia | Uso |
|---|---|---|
| Cliente → serviço | **REST síncrono**, pelo Kong | Comandos e consultas de quem espera resposta imediata |
| Serviço → serviço, fluxo da Saga | **RabbitMQ**: exchange `autogiro.commands` (direct) e `autogiro.events` (topic) | Todos os comandos e eventos ([mensageria.md](../arquitetura/mensageria.md)) |
| Serviço → serviço, síncrono | **Um único ponto:** OS → Execution `GET /api/v1/parts/{id}` ao abrir a OS, com timeout de 2 s | Avisar o atendente na hora que a peça não existe. Se o Execution não responder, a OS abre e a Saga valida depois |
| Billing → Mercado Pago | REST síncrono | API externa |

No cluster, o **RabbitMQ** é provisionado pela Terraform do
[autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/terraform/rabbitmq.tf):
imagem oficial `rabbitmq:4.0-management` fixada por digest, um Deployment de uma réplica
(estratégia `Recreate`), usuário próprio no lugar do `guest` e métricas pelo plugin
`rabbitmq_prometheus`. Ele roda **sem volume persistente** (`emptyDir`), porque o EKS não traz
o driver de EBS instalado. Localmente, a mesma imagem sobe no `docker-compose`, e cada serviço
declara a mesma topologia ao conectar.

## Consequências

**Positivas**
- Um participante fora do ar **atrasa** a Saga, mas não a quebra: as mensagens esperam na
  fila.
- Picos de abertura de OS são absorvidos pelas filas.
- Retentativa e DLQ ficam na infraestrutura de mensageria, não espalhadas pelo código de
  negócio.
- O painel do RabbitMQ mostra filas e mensagens em tempo real.

**Negativas**
- Consistência eventual: logo depois de abrir a OS, o orçamento ainda não existe. A API
  deixa isso explícito com o status e com `GET /service-orders/{id}/saga`.
- Mais um componente para operar no cluster (256 Mi de memória reservados, limite de 512 Mi).
- Sem volume persistente, mensagens que estavam na fila se perdem se o pod do broker for
  recriado. O outbox e o reenvio de etapa parada cobrem parte desse risco
  ([ADR-009](ADR-009-confiabilidade-das-mensagens.md)).
- Depurar um fluxo exige rastreamento distribuído
  ([ADR-011](ADR-011-rastreamento-opentelemetry-new-relic.md)).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Amazon SQS/SNS | Gerenciado e sem ocupar o cluster, mas exige IRSA (permissões que a conta pode bloquear) e um emulador (LocalStack) para desenvolver localmente |
| Kafka | Log distribuído pensado para alto volume e replay. Pesado demais para nós pequenos e para o volume do projeto |
| Só REST síncrono (manter a decisão anterior) | Não atende à exigência de mensageria assíncrona e deixaria a Saga refém da disponibilidade simultânea dos 3 serviços |
| Nenhuma chamada síncrona entre serviços | Seria mais puro, mas o atendente só descobriria uma peça inexistente depois, por um cancelamento. A chamada única, com timeout e fallback, não cria dependência dura |
