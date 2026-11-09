# ADR-008: Saga orquestrada, com o orquestrador no OS Service

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira

## Contexto

Abrir uma OS, reservar peças, orçar, aprovar, cobrar e enfileirar são transações locais em 3
bancos diferentes. Não existe transação distribuída entre eles; se uma etapa falha, as
anteriores precisam ser **desfeitas por compensação**. O enunciado aceita duas estratégias:
**orquestração** (um coordenador central) ou **coreografia** (serviços reagindo a eventos uns
dos outros), e pede a justificativa da escolha.

## Comparação

| Critério | Orquestração | Coreografia |
|---|---|---|
| Onde o fluxo está descrito | Num lugar só (o orquestrador) | Espalhado: cada serviço sabe a quem reagir |
| Compensação | O orquestrador sabe exatamente o que já foi feito e o que desfazer | Cada serviço precisa ouvir as falhas dos outros e decidir sozinho se compensa |
| Saber "onde está esta OS?" | Uma consulta: `GET /service-orders/{id}/saga` | Reconstruir pelos eventos de 3 serviços |
| Acoplamento | Participantes não se conhecem; o orquestrador conhece todos | Serviços conhecem os eventos uns dos outros |
| Risco | O orquestrador concentrar regra de negócio | Dependências cíclicas e fluxo difícil de entender |
| Testar o fluxo completo | Testes do orquestrador com participantes simulados | Exige subir todos os serviços |

## Decisão

**Saga orquestrada**, com o orquestrador **dentro do OS Service**:

1. **O fluxo tem seis etapas depois da abertura, três esperas (aprovação e pagamento pelo
   cliente, andamento pela oficina) e oito caminhos de falha** (ver
   [saga.md](../arquitetura/saga.md)). Com coreografia, a lógica de "o que desfazer quando o
   pagamento é recusado" ficaria repartida entre Billing e Execution. Com orquestração, é uma
   linha da matriz de compensações, decidida num só lugar
   ([`app/domain/entities/saga.py`](../../app/domain/entities/saga.py)).
2. **O OS Service já é o dono do ciclo de vida da OS.** O estado da Saga, o status da OS e o
   próximo comando (no outbox) mudam **na mesma transação local**, sem um 4º serviço para
   manter.
3. **Visibilidade:** o estado da Saga é uma tabela consultável por
   `GET /service-orders/{id}/saga`, e os spans de cada mensagem levam `autogiro.saga_id`, o
   que permite seguir uma OS no New Relic.

Para o orquestrador não virar um "serviço Deus": **ele só coordena**. Regras de domínio
(como reservar, como orçar, como estornar) ficam nos participantes, que recebem comandos
genéricos e não conhecem a Saga.

## Consequências

**Positivas**
- Matriz de compensações explícita e testável em um lugar: testes unitários da decisão,
  testes de integração do orquestrador e cenários BDD com os participantes simulados.
- Participantes simples: comando → transação local → evento.
- Um endpoint mostra o estado de qualquer Saga, etapa por etapa, com o número de envios de
  cada comando.

**Negativas**
- O OS Service passa a depender dos contratos de mensagem dos outros dois: mudar um evento
  exige versionamento (`version` no envelope).
- O orquestrador é ponto central do fluxo. Se cair, as Sagas param (mas não se perdem: o
  outbox e as filas guardam tudo até ele voltar).

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| Coreografia | Compensação espalhada; mais difícil de testar, rastrear e explicar |
| Orquestrador como 4º serviço | Mais um repositório, pipeline e banco; o estado da Saga ficaria separado do status da OS, exigindo sincronizá-los |
| Engine de workflow (Temporal, Step Functions) | Infraestrutura nova a aprender e operar no prazo da entrega; Step Functions esbarraria nas restrições da conta AWS |
