# ADR-009: Confiabilidade das mensagens — outbox, idempotência, retry e DLQ

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira

## Contexto

Uma Saga só é confiável se nenhuma mensagem se perder e nenhum efeito se repetir. O
RabbitMQ entrega **pelo menos uma vez** (*at-least-once*): pode entregar duplicado, e uma
mensagem publicada fora da transação do banco pode se perder se o serviço cair entre o
commit e a publicação. No cluster, o broker ainda roda sem volume persistente
([ADR-007](ADR-007-rest-na-borda-rabbitmq-entre-servicos.md)), então um reinício do pod
perde o que estava nas filas.

## Decisão

| Mecanismo | Onde | O que garante |
|---|---|---|
| **Outbox transacional** | **Os 3 serviços** (tabela `outbox` no OS e no Billing, coleção `outbox` no Execution) | A mudança de estado e a mensagem de saída são gravadas na mesma transação; um publicador em segundo plano entrega as pendentes a cada 1 s. Comando ou evento gravado nunca se perde |
| **Contexto do trace no outbox** | Os 3 serviços (`trace_context`) | A mensagem é publicada com o `traceparent` do trace em que nasceu, e não do laço que publica ([ADR-011](ADR-011-rastreamento-opentelemetry-new-relic.md)) |
| **Deduplicação por `message_id`** | Os 3 serviços (`processed_messages`) | A mesma mensagem entregue duas vezes produz efeito uma vez. O registro é gravado na mesma transação do efeito |
| **Idempotência de negócio por `order_id`** | Billing e Execution | Reserva, orçamento e execução são únicos por OS; repetir um comando não refaz o efeito, só grava de novo o mesmo evento de resposta |
| **Reenvio de etapa parada** | Orquestrador | A cada 30 s, procura etapas com comando enviado e sem resposta há 120 s (`SAGA_STEP_RETRY_SECONDS`) e reenvia o comando, com novo `message_id`. Vale também para as compensações |
| **Trava na linha da Saga** | Orquestrador | `SELECT … FOR UPDATE` na Saga: dois eventos da mesma OS em réplicas diferentes são processados um depois do outro |
| **Retry com atraso + DLQ** | Todas as filas | 3 novas tentativas com 5 s de intervalo; depois, DLQ e alerta. Envelope inválido vai direto para a DLQ |
| **Durabilidade** | RabbitMQ | Exchanges e filas duráveis, mensagens persistentes, *publisher confirms*, ack só depois do commit |

O desenho inicial previa outbox só no orquestrador, com Billing e Execution publicando logo
depois do commit e o reenvio de etapa parada cobrindo a perda. Na implementação, o outbox foi
para os três serviços: os eventos das etapas de espera (aprovação, pagamento, andamento da
execução) não nascem de um comando, e o reenvio não teria como recuperá-los.

Com duas réplicas, o publicador do OS e do Billing lê as pendentes com
`FOR UPDATE SKIP LOCKED`, e cada réplica publica só o que nenhuma outra pegou. O do Execution
(MongoDB) não reserva as mensagens, então um evento pode sair duas vezes; a deduplicação por
`message_id` no orquestrador descarta a cópia.

## Consequências

**Positivas**
- Nenhuma mensagem gravada se perde no caminho até o broker, em nenhum dos três serviços.
- Nenhum efeito duplicado nos cenários de falha previstos
  ([saga.md](../arquitetura/saga.md), "Falhas de infraestrutura").
- Mensagens envenenadas não travam a fila.

**Negativas**
- Tabelas e coleções extras (`outbox`, `processed_messages`) e um publicador em segundo plano
  em cada serviço.
- Uma mensagem perdida no broker (reinício do pod sem volume) só é recuperada se for um
  comando: o reenvio de etapa parada a refaz depois de 120 s. Um evento de etapa de espera
  perdido no broker deixa a OS parada naquela etapa.
- O reenvio de etapa parada não tem limite de tentativas: enquanto o participante não
  responder, o comando sai de novo a cada 120 s.
- Reprocessar a DLQ é manual, pelo painel do RabbitMQ.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Outbox só no orquestrador | Era o desenho inicial. Não cobre os eventos que nascem de uma requisição REST ou do monitor de pagamentos, que não têm comando para ser reenviado |
| CDC (Debezium) lendo o log do banco | Infraestrutura pesada (Kafka Connect) para o porte do projeto |
| Confiar só no ack do RabbitMQ | Não cobre a queda entre commit e publicação |
