# AutoGiro · OS Service

[![CI/CD](https://github.com/Figueiraa/autogiro-os-service/actions/workflows/ci-cd.yml/badge.svg)](https://github.com/Figueiraa/autogiro-os-service/actions/workflows/ci-cd.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=alert_status)](https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-os-service)
[![Cobertura](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=coverage)](https://sonarcloud.io/component_measures?id=Figueiraa_autogiro-os-service&metric=coverage)

> Microsserviço de **Atendimento** da plataforma AutoGiro, para redes de oficinas mecânicas.
> Tech Challenge **Fase 4** (13SOAT): arquitetura de microsserviços com Saga Pattern.

| | |
|---|---|
| **É dono de** | Clientes, veículos, catálogo de serviços, ordens de serviço e histórico de status |
| **Papel na Saga** | **Orquestrador**: coordena as etapas e as compensações |
| **Banco** | PostgreSQL (Neon) |
| **Comunicação** | REST pelo Kong (borda) · RabbitMQ (comandos e eventos da Saga) |

## Documentação

A arquitetura da plataforma fica em [`docs/`](docs/README.md), neste repositório, porque é
aqui que mora o orquestrador da Saga.

| Documento | Conteúdo |
|---|---|
| [Visão geral](docs/arquitetura/visao-geral.md) | Diagrama geral, os três serviços e as estratégias de comunicação |
| [Posse dos dados](docs/arquitetura/posse-dos-dados.md) | Dono de cada dado, modelo de cada banco e o que é cópia |
| [Máquinas de estado](docs/arquitetura/maquina-de-estados.md) | OS, Saga, orçamento, pagamento, reserva e execução |
| [Saga](docs/arquitetura/saga.md) | Etapas, compensações, idempotência, isolamento, falhas e sequências |
| [Mensageria](docs/arquitetura/mensageria.md) | Envelope, topologia no RabbitMQ, catálogo de mensagens, retry e DLQ |

Decisões de arquitetura:
[ADR-005](docs/adr/ADR-005-tres-microsservicos.md) (três microsserviços) ·
[ADR-006](docs/adr/ADR-006-banco-por-servico.md) (banco por serviço) ·
[ADR-007](docs/adr/ADR-007-rest-na-borda-rabbitmq-entre-servicos.md) (REST na borda, RabbitMQ entre serviços) ·
[ADR-008](docs/adr/ADR-008-saga-orquestrada.md) (Saga orquestrada) ·
[ADR-009](docs/adr/ADR-009-confiabilidade-das-mensagens.md) (confiabilidade das mensagens) ·
[ADR-011](docs/adr/ADR-011-rastreamento-opentelemetry-new-relic.md) (OpenTelemetry e New Relic) ·
[ADR-013](docs/adr/ADR-013-lambda-no-contexto-de-atendimento.md) (Lambda no contexto de Atendimento).
O [ADR-010](https://github.com/Figueiraa/autogiro-billing-service/blob/main/docs/adr/ADR-010-mercado-pago-checkout-pro.md)
fica no Billing, e o [ADR-012](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/docs/adr/ADR-012-kubernetes-no-eks.md)
e as [APIs e rotas no Kong](https://github.com/Figueiraa/autogiro-infra-k8s/blob/main/docs/apis-e-gateway.md),
no `autogiro-infra-k8s`.

## Estrutura

Clean Architecture (domínio, aplicação, infraestrutura e interfaces), sondas de saúde,
métricas Prometheus, logs JSON, validação do JWT emitido pelo `autogiro-auth` e pipeline com
testes, SonarCloud e imagem Docker.

```mermaid
flowchart LR
    K[Kong · JWT] --> API
    subgraph OS["OS Service"]
        API[API REST<br/>interfaces/http] --> UC[Casos de uso]
        UC -- "abre a OS e<br/>inicia a Saga" --> ORQ[SagaOrchestrator]
        CONS[Consumidor<br/>os.saga-events] --> ORQ
        ORQ --> DOM[Domínio<br/>ServiceOrder · Saga]
        PUB[Publicador do outbox<br/>a cada 1 s]
        RET[Reenvio de etapas<br/>paradas · a cada 30 s]
    end
    UC & ORQ & PUB & RET --> DB[(PostgreSQL · Neon<br/>cadastros, OS, Saga,<br/>outbox, processed_messages)]
    API -. "GET /api/v1/parts/{id}<br/>timeout 2 s" .-> EX[Execution Service]
    PUB -- comandos --> CMD{{autogiro.commands}}
    EVT{{autogiro.events}} -- "execution.# · billing.#" --> CONS
    API & CONS & PUB -. "traces OTLP" .-> NR[New Relic]
```

A regra da Saga é uma decisão pura no domínio (`app/domain/entities/saga.py`): recebe um evento
e devolve os comandos e os status a aplicar. O `SagaOrchestrator` (`app/application/use_cases/`)
executa a decisão numa transação; o consumidor, o publicador do outbox e o reenvio de etapas
paradas rodam junto com a API (`app/infrastructure/messaging/`).

## API

Todas as rotas exigem o JWT do `autogiro-auth`, exceto o acompanhamento público da OS.
Documentação interativa em `/docs` (Swagger).

| Método | Rota | Descrição |
|---|---|---|
| `GET` `POST` | `/api/v1/clients` | Listar e cadastrar clientes |
| `GET` `PATCH` `DELETE` | `/api/v1/clients/{id}` | Detalhar, alterar e remover |
| `GET` `POST` | `/api/v1/vehicles` | Listar e cadastrar veículos |
| `GET` `PATCH` `DELETE` | `/api/v1/vehicles/{id}` | Detalhar, alterar e remover |
| `GET` | `/api/v1/vehicles/client/{client_id}` | Veículos de um cliente |
| `GET` `POST` | `/api/v1/service-types` | Catálogo de serviços |
| `GET` `PATCH` `DELETE` | `/api/v1/service-types/{id}` | Detalhar, alterar e remover |
| `POST` | `/api/v1/service-orders` | Abre a OS com serviços do catálogo e peças pedidas |
| `GET` | `/api/v1/service-orders` | OS ativas, da que exige ação primeiro (filtro opcional por `status`) |
| `GET` | `/api/v1/service-orders/{id}` | Detalhe da OS |
| `GET` | `/api/v1/service-orders/{id}/history` | Histórico de status, com motivo e origem de cada mudança |
| `GET` | `/api/v1/service-orders/{id}/saga` | Estado da Saga: etapa atual, situação de cada etapa e das compensações |
| `POST` | `/api/v1/service-orders/{id}/deliver` | Entrega do veículo (`FINALIZADA → ENTREGUE`) |
| `GET` | `/api/v1/service-orders/{id}/status` | **Pública:** acompanhamento pelo cliente |
| `GET` | `/api/v1/service-orders/metrics/average-execution-time` | Tempo médio do reparo |

### Ciclo de vida da OS

```
RECEBIDA → AGUARDANDO_APROVACAO → AGUARDANDO_PAGAMENTO → PAGA → NA_FILA
        → EM_DIAGNOSTICO → EM_EXECUCAO → FINALIZADA → ENTREGUE

Qualquer etapa antes de FINALIZADA → EM_CANCELAMENTO → CANCELADA (com o motivo)
```

A abertura não calcula orçamento nem mexe em estoque. Peças e estoque pertencem ao
Execution Service; orçamento e pagamento, ao Billing Service. Aqui ficam o pedido e as cópias
que voltam de lá (nome e preço da peça, total do orçamento, link de pagamento).

Ao abrir a OS, as peças são conferidas no catálogo do Execution Service
(`EXECUTION_SERVICE_URL`, com timeout de 2 s). Peça que não existe no catálogo recusa a abertura
com `422`. Se o Execution não responder, a OS é aberta assim mesmo e a peça é validada depois,
na reserva feita pela Saga.

## Saga

O fluxo da OS atravessa os três serviços e cada etapa é uma transação local num banco
diferente. Não há transação distribuída: se uma etapa falha, as anteriores são desfeitas por
**compensação**.

### Estratégia: Saga orquestrada

A consistência é garantida por uma **Saga orquestrada**, com o orquestrador dentro deste
serviço. Ele envia **comandos** aos participantes (Execution e Billing) pelo RabbitMQ, recebe
os **eventos** de resposta e decide o próximo passo ou as compensações. Os participantes não
conhecem a Saga: recebem um comando, fazem uma transação local e respondem com um evento.

### Por que orquestração, e não coreografia

Na coreografia não há coordenador: cada serviço reage aos eventos dos outros. Ela foi
descartada por quatro motivos:

- **O fluxo tem 6 etapas, duas esperas pelo cliente e 8 caminhos de falha.** Com coreografia,
  a regra de "o que desfazer quando o pagamento é recusado" ficaria espalhada entre Billing e
  Execution, cada um ouvindo as falhas do outro e decidindo sozinho se compensa. Com um
  orquestrador, ela é uma linha de uma tabela, testada num lugar só.
- **Este serviço já é o dono do ciclo de vida da OS:** o status da OS, o estado da Saga e o
  próximo comando mudam na **mesma transação local**, sem um quarto serviço para manter.
- **Visibilidade:** `GET /service-orders/{id}/saga` mostra em que ponto está qualquer OS. Na
  coreografia, seria preciso reconstruir o estado pelos eventos dos três serviços.
- **Menos acoplamento entre participantes:** Billing e Execution não se conhecem; só o
  orquestrador conhece os dois. Na coreografia, cada serviço depende dos eventos dos outros, com
  risco de dependência cíclica.

O custo assumido é concentrar o fluxo num serviço. Para ele não virar um "serviço Deus", o
orquestrador **só coordena**: como reservar, orçar ou estornar é regra dos participantes. A
comparação completa está no [ADR-008](docs/adr/ADR-008-saga-orquestrada.md).

### Etapas e compensações

| Etapa | Participante | Comando | Sucesso | Falha |
|---|---|---|---|---|
| Reservar peças | Execution | `ReservarPecas` | `PecasReservadas` | `PecasIndisponiveis` |
| Gerar orçamento | Billing | `GerarOrcamento` | `OrcamentoGerado` | `OrcamentoFalhou` |
| Aprovação do cliente | Billing | — | `OrcamentoAprovado` | `OrcamentoRecusado`, `OrcamentoExpirado` |
| Pagamento | Billing | — | `PagamentoConfirmado` | `PagamentoRecusado`, `PagamentoExpirado` |
| Enfileirar execução | Execution | `EnfileirarExecucao` | `ExecucaoEnfileirada` | `EnfileiramentoRecusado` |
| Execução | Execution | — | `DiagnosticoIniciado` → `ReparoIniciado` → `ExecucaoFinalizada` | `ExecucaoCancelada` |

Quando uma etapa falha, a OS vai para `EM_CANCELAMENTO` e só o que foi feito é desfeito, com os
comandos enviados em paralelo:

- `LiberarPecas`, se as peças chegaram a ser reservadas;
- `CancelarCobranca`, se o orçamento chegou a ser gerado (o Billing estorna se já houve pagamento).

A OS só vai para `CANCELADA` quando **todas** as compensações confirmam. Se o Mercado Pago
recusar o estorno (o sandbox recusa estorno por API), o Billing confirma a compensação com
`estorno_pendente: true` e o estorno é feito pelo painel do Mercado Pago, sem travar a Saga.
Detalhes, matriz de compensações e diagramas de sequência em [docs/arquitetura/saga.md](docs/arquitetura/saga.md).

### Comandos publicados e eventos consumidos

**Comandos publicados** em `autogiro.commands` (pelo outbox):

| Comando | Para | Routing key |
|---|---|---|
| `ReservarPecas` | Execution | `execution.reservar-pecas` |
| `LiberarPecas` (compensação) | Execution | `execution.liberar-pecas` |
| `EnfileirarExecucao` | Execution | `execution.enfileirar-execucao` |
| `GerarOrcamento` | Billing | `billing.gerar-orcamento` |
| `CancelarCobranca` (compensação) | Billing | `billing.cancelar-cobranca` |

**Eventos consumidos** da fila `os.saga-events` (ligada a `autogiro.events` por `execution.#`
e `billing.#`):

| Origem | Eventos |
|---|---|
| Execution | `PecasReservadas`, `PecasIndisponiveis`, `PecasLiberadas`, `ExecucaoEnfileirada`, `EnfileiramentoRecusado`, `DiagnosticoIniciado`, `ReparoIniciado`, `ExecucaoFinalizada`, `ExecucaoCancelada` |
| Billing | `OrcamentoGerado`, `OrcamentoFalhou`, `OrcamentoAprovado`, `OrcamentoRecusado`, `OrcamentoExpirado`, `PagamentoConfirmado`, `PagamentoRecusado`, `PagamentoExpirado`, `CobrancaCancelada` |

Payloads e o catálogo completo em [docs/arquitetura/mensageria.md](docs/arquitetura/mensageria.md).

### Confiabilidade

| Mecanismo | O que garante |
|---|---|
| Outbox transacional | O comando é gravado na mesma transação da mudança de estado e publicado depois: nunca se perde |
| Registro de mensagens processadas | A mesma mensagem entregue duas vezes produz efeito uma vez |
| Trava na linha da Saga | Duas confirmações da mesma Saga em réplicas diferentes não se sobrepõem |
| Reenvio de etapa parada | Comando sem resposta há 120 s é reenviado; o participante é idempotente por OS |
| Retry com atraso e DLQ | Falha técnica: 3 novas tentativas a cada 5 s, depois a fila `os.saga-events.dlq` |
| Bloqueio semântico | De `EM_CANCELAMENTO` a OS só vai para `CANCELADA`: a entrega exige `FINALIZADA` e eventos de avanço de uma Saga que está compensando são ignorados |

### Mensageria (RabbitMQ)

| Recurso | Tipo | Uso |
|---|---|---|
| `autogiro.commands` | exchange direct | Comandos do orquestrador (`execution.reservar-pecas`, `billing.gerar-orcamento`…) |
| `autogiro.events` | exchange topic | Eventos dos participantes (`execution.#`, `billing.#`) |
| `os.saga-events` | fila | Eventos consumidos por este serviço, com `.retry` e `.dlq` |

Todas as mensagens usam o mesmo envelope JSON: `message_id`, `type`, `kind`, `version`,
`saga_id`, `order_id`, `causation_id`, `occurred_at`, `producer` e `payload`.

## Observabilidade

Métricas Prometheus em `GET /metrics` (raspadas pelo agente do New Relic no cluster) e
**rastreamento distribuído com OpenTelemetry**, exportado direto para o New Relic por OTLP.

| Variável | Uso |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://otlp.nr-data.net`. Sem ela, nada é exportado e o serviço funciona igual |
| `NEW_RELIC_LICENSE_KEY` | License key de ingestão, enviada no cabeçalho `api-key` |
| `DEPLOYMENT_ENVIRONMENT` | Ambiente nos traces (`local`, `producao`) |

Como cada trecho do fluxo de uma OS vira **um trace só** entre os serviços:

- HTTP de entrada e de saída (httpx, inclusive a consulta ao catálogo de peças do Execution) e o PostgreSQL (SQLAlchemy) são instrumentados automaticamente; as chamadas HTTP levam
  o cabeçalho `traceparent` (W3C).
- Cada mensagem gravada no outbox guarda o contexto do trace em que nasceu. O publicador envia esse
  contexto nos cabeçalhos AMQP e o consumidor continua o mesmo trace. Assim o trace segue o fluxo
  de negócio, e não o laço que publica o outbox.
- Spans de mensagem levam `autogiro.saga_id`, `autogiro.order_id`, `autogiro.message.type` e o
  desfecho da entrega (`done`, `retry` ou `dead_letter`); o span de cada evento registra também o estado da Saga depois dele (`autogiro.saga.state`) e o motivo da compensação.
- Os logs JSON levam `trace.id` e `span.id` (nomes que o New Relic usa para ligar log e trace) e,
  durante o processamento de uma mensagem, `saga_id`, `order_id` e `message_type`.
- Consultas ao banco feitas pelos laços em segundo plano (outbox, reenvio de etapas paradas, sondas de saúde) não abrem
  trace próprio: só aparecem dentro de uma requisição ou mensagem.
- A Saga inteira não é um trace só: cada ação humana (aprovar o orçamento, o mecânico avançar a
  execução) é uma requisição nova, e o pagamento é confirmado pelo monitor do Billing, fora de
  uma requisição. Os trechos de uma mesma OS se ligam pelo `autogiro.saga_id` e pelo
  `autogiro.order_id` ([ADR-011](docs/adr/ADR-011-rastreamento-opentelemetry-new-relic.md)).

O painel **AutoGiro — Saga e rastreamento** e o alerta de mensagem na DLQ ficam no
[autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s), em Terraform.

## Banco de dados

PostgreSQL próprio, num projeto Neon só do serviço (`terraform/`). O schema é versionado em
`migrations/` e aplicado por `python -m app.infrastructure.migrations`, que roda cada arquivo
uma única vez, em ordem.

| Migration | Conteúdo |
|---|---|
| `001_schema_inicial.sql` | Clientes, veículos, catálogo, ordens de serviço e histórico de status |
| `002_contrato_autenticacao.sql` | View `auth.clients` e permissões do usuário somente leitura `auth_reader` da Lambda de autenticação (no Neon, o usuário é criado pelo `terraform/` e o `search_path` vai na URL de conexão, saída `auth_database_url`) |
| `003_dados_de_demonstracao.sql` | Clientes, veículos e serviços de exemplo |
| `004_saga.sql` | Estado das Sagas, outbox e mensagens processadas |
| `005_contexto_de_rastreamento.sql` | Coluna `trace_context` no outbox: o contexto do trace em que cada mensagem nasceu |

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

pytest                                  # testes + cobertura (mínimo de 80%)
ruff check app tests scripts            # lint
bandit -r app -ll                       # análise de segurança

cp .env.example .env                    # defina o JWT_SECRET
docker compose up --build               # aplica as migrations e sobe a API em http://localhost:8001
```

Os testes rodam sobre SQLite em memória. Os testes das migrations e da mensageria precisam de
PostgreSQL e RabbitMQ reais e rodam quando `TEST_POSTGRES_URL` e `TEST_RABBITMQ_URL` estão
definidas (no CI, containers do PostgreSQL 17 e do RabbitMQ 4):

```bash
TEST_POSTGRES_URL=postgresql+asyncpg://autogiro:autogiro@localhost:5433/autogiro_os \
TEST_RABBITMQ_URL=amqp://guest:guest@localhost:5672/ pytest
```

O `JWT_SECRET` é obrigatório e não tem valor padrão no código: o serviço não sobe sem ele.

### Cenários de negócio (BDD)

Os cenários da Saga estão escritos em Gherkin, em português, em
[`tests/bdd/features/saga_ordem_de_servico.feature`](tests/bdd/features/saga_ordem_de_servico.feature):

| Cenário | O que comprova |
|---|---|
| Caminho feliz, da abertura à entrega | Cada comando sai para o serviço certo e cada evento avança a OS até `ENTREGUE` |
| Peça indisponível | A OS é cancelada sem compensações, porque nada chegou a ser feito |
| Orçamento recusado | A reserva de peças é liberada e a cobrança, cancelada |
| Pagamento recusado | Mesmas compensações, com o motivo `PAGAMENTO_RECUSADO` |
| Oficina lotada depois do pagamento | A cobrança é cancelada com estorno do valor pago |

Os passos usam a API real do serviço e o orquestrador de verdade; só as respostas do Execution e
do Billing são simuladas, publicando os mesmos eventos que eles publicariam. Rodam junto com o resto
da suíte, ou sozinhos com `pytest -m bdd`.

### Plataforma completa, localmente

[`docker-compose.ecossistema.yml`](docker-compose.ecossistema.yml) sobe os três microsserviços, cada um
com o seu banco (PostgreSQL para OS e Billing, MongoDB em replica set para o Execution), e o RabbitMQ
que liga a Saga:

```bash
cp .env.ecossistema.example .env.ecossistema   # JWT_SECRET e token de teste do Mercado Pago
docker compose -f docker-compose.ecossistema.yml --env-file .env.ecossistema up -d
```

| Serviço | Endereço |
|---|---|
| OS Service | <http://localhost:8001/docs> |
| Billing Service | <http://localhost:8002/docs> |
| Execution Service | <http://localhost:8003/docs> |
| Painel do RabbitMQ | <http://localhost:15672> (guest/guest) |

A coleção [`docs/postman/autogiro-fluxo-completo.postman_collection.json`](docs/postman/autogiro-fluxo-completo.postman_collection.json)
percorre o fluxo inteiro nos três serviços: abertura, Saga, orçamento, pagamento no Mercado Pago,
execução na oficina e entrega. Ela guarda o id da OS e o link de pagamento em variáveis, então basta
rodar as pastas na ordem.

Falhas para experimentar com a plataforma no ar:

- **Serviço fora do ar:** `docker compose ... stop execution-service` no meio da Saga. Os comandos
  esperam na fila e a OS continua de onde parou quando o serviço volta.
- **Oficina lotada:** `EXECUTION_QUEUE_CAPACITY=0` no `.env.ecossistema`. Depois do pagamento, a OS é
  cancelada e a cobrança, estornada.
- **Mensagem inválida:** cai em `os.saga-events.dlq` depois das tentativas, sem travar a fila.

## Kubernetes

Manifestos em [`k8s/`](k8s/), aplicados com `kubectl apply -k k8s/` no namespace `autogiro` do
cluster EKS criado pelo [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s).

| Recurso | Configuração |
|---|---|
| Deployment | 2 réplicas, usuário sem privilégios, sistema de arquivos somente leitura, sondas em `/health/live` e `/health/ready`. Migrations aplicadas por um *init container* antes da API subir. |
| Service | ClusterIP na porta 80 |
| HPA | 2 a 6 réplicas, por CPU (70%) e memória (80%) |
| Ingress (Kong) | `/api/v1/clients`, `/vehicles`, `/service-types` e `/service-orders`, com JWT. O acompanhamento público `GET /api/v1/service-orders/{id}/status` tem rota própria, sem token. |
| ConfigMap | Configuração própria do serviço |

A configuração da plataforma vem do Secret e do ConfigMap `autogiro-plataforma`, criados pelo
`autogiro-infra-k8s`: `JWT_SECRET`, `RABBITMQ_URL` (broker no cluster), `NEW_RELIC_LICENSE_KEY` e o
endpoint OTLP. O Secret próprio do serviço, `os-service-secret`, tem a forma de
[`k8s/secret.example.yaml`](k8s/secret.example.yaml):

| Chave | Conteúdo |
|---|---|
| `DATABASE_URL` | Neon do OS Service (saída `database_url` do `terraform/`) |

## Pipeline (`.github/workflows/ci-cd.yml`)

| Job | O que faz | Bloqueia o merge? |
|---|---|---|
| **Lint & Test** | ruff, bandit e pytest com cobertura mínima de 80% | Sim |
| **SonarCloud** | Análise de qualidade com quality gate bloqueante | Sim |
| **Build & Push image** | Imagem Docker + scan de vulnerabilidades (trivy). Na `main`, publica no GHCR para amd64 e arm64 | Sim |
| **Deploy (EKS)** | Na `main`, com o cluster no ar: atualiza o Secret do serviço, aplica `k8s/` com a imagem do commit, espera o rollout e roda o smoke test | Não (só existe na `main`) |

A `main` é protegida: só recebe código por pull request, com as três checagens verdes.

### Deploy contínuo

Todo merge na `main` publica o serviço no EKS, sem passo manual:

1. A imagem sai com a tag do commit (imutável); o `k8s/kustomization.yaml` passa a apontar para ela.
2. O Secret `os-service-secret` é atualizado a partir dos secrets do GitHub (`DATABASE_URL`).
3. `kubectl apply -k k8s/` e `kubectl rollout status`. Se o rollout não terminar em 5 minutos, a
   pipeline faz `rollout undo` e falha: a versão anterior continua atendendo.
4. Smoke test: o Deployment roda a imagem do commit com todas as réplicas disponíveis, a prontidão
   (banco e broker) responde de dentro do pod e a rota está publicada no Kong (401 sem token).
5. O resumo da execução registra commit, imagem, revisão do Deployment e os pods em execução.

O job só roda com a variável `DEPLOY_ENABLED` do repositório em `true`. Ela é ligada pelo script que
sobe o cluster, que também dispara a pipeline da `main` (`workflow_dispatch`) para a primeira
publicação, e desligada pelo script que o destrói: com o cluster desligado o job aparece como
*skipped* e a pipeline segue verde. As credenciais da AWS (`AWS_ACCESS_KEY_ID` e
`AWS_SECRET_ACCESS_KEY`) também ficam nos secrets do repositório.

## Qualidade e cobertura

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=alert_status)](https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-os-service)
[![Cobertura](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=coverage)](https://sonarcloud.io/component_measures?id=Figueiraa_autogiro-os-service&metric=coverage)
[![Bugs](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=bugs)](https://sonarcloud.io/project/issues?id=Figueiraa_autogiro-os-service&types=BUG)
[![Vulnerabilidades](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=vulnerabilities)](https://sonarcloud.io/project/issues?id=Figueiraa_autogiro-os-service&types=VULNERABILITY)
[![Code smells](https://sonarcloud.io/api/project_badges/measure?project=Figueiraa_autogiro-os-service&metric=code_smells)](https://sonarcloud.io/project/issues?id=Figueiraa_autogiro-os-service&types=CODE_SMELL)

Cobertura de **98,7%** na `main`, com 317 testes, quality gate aprovado e nenhum bug,
vulnerabilidade ou code smell aberto. A pipeline exige no mínimo 80% (`--cov-fail-under=80`) e o
quality gate do SonarCloud bloqueia o merge.

![Painel do SonarCloud](docs/img/sonarcloud.png)

- **Painel do SonarCloud:** <https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-os-service> (os badges acima são atualizados a cada análise).
- **Relatório de cada execução:** aba *Summary* da pipeline no GitHub Actions, com o quadro de testes e cobertura, e o artefato `test-and-coverage-reports` (JUnit, `coverage.xml` e HTML).

## Swagger e Postman

- **Swagger:** `http://localhost:8001/docs` com o serviço rodando localmente; no cluster,
  `kubectl -n autogiro port-forward svc/os-service 8001:80` e o mesmo endereço.
- [`docs/openapi.json`](docs/openapi.json): a especificação OpenAPI exportada da aplicação.
- [`docs/postman/os-service.postman_collection.json`](docs/postman/os-service.postman_collection.json): todas as rotas do serviço,
  com as variáveis `base_url`, `token` e os ids usados nos caminhos.
- [`docs/postman/autogiro-fluxo-completo.postman_collection.json`](docs/postman/autogiro-fluxo-completo.postman_collection.json): o fluxo inteiro da OS pelos três serviços, da abertura à entrega.

## Repositórios da plataforma

| Repositório | Papel |
|---|---|
| [autogiro-os-service](https://github.com/Figueiraa/autogiro-os-service) | Atendimento e orquestração da Saga |
| [autogiro-billing-service](https://github.com/Figueiraa/autogiro-billing-service) | Orçamento, pagamento (Mercado Pago) e estorno |
| [autogiro-execution-service](https://github.com/Figueiraa/autogiro-execution-service) | Estoque de peças e fila de execução |
| [autogiro-auth](https://github.com/Figueiraa/autogiro-auth) | Lambda de autenticação por CPF |
| [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s) | Cluster EKS, Kong, RabbitMQ e New Relic |
