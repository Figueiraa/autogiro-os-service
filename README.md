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

## Estrutura

Clean Architecture (domínio, aplicação, infraestrutura e interfaces), sondas de saúde,
métricas Prometheus, logs JSON, validação do JWT emitido pelo `autogiro-auth` e pipeline com
testes, SonarCloud e imagem Docker.

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
(`EXECUTION_SERVICE_URL`, com timeout de 2 s). Se ele não responder, a OS é aberta assim mesmo
e a peça é validada depois, no fluxo da OS.

## Saga

O fluxo da OS atravessa os três serviços e cada etapa é uma transação local num banco
diferente. A consistência é garantida por uma **Saga orquestrada**, com o orquestrador dentro
deste serviço.

### Por que orquestração

- **O fluxo tem 6 etapas, duas esperas pelo cliente e 8 caminhos de falha.** Com coreografia,
  a regra de "o que desfazer quando o pagamento é recusado" ficaria espalhada entre Billing e
  Execution. Com um orquestrador, ela é uma linha de uma tabela, testada num lugar só.
- **Este serviço já é o dono do ciclo de vida da OS:** o status da OS, o estado da Saga e o
  próximo comando mudam na **mesma transação local**, sem um quarto serviço para manter.
- **Visibilidade:** `GET /service-orders/{id}/saga` mostra em que ponto está qualquer OS.
- O orquestrador **só coordena**: como reservar, orçar ou estornar é regra dos participantes,
  que recebem comandos e respondem com eventos sem conhecer a Saga.

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

A OS só vai para `CANCELADA` quando **todas** as compensações confirmam.

### Confiabilidade

| Mecanismo | O que garante |
|---|---|
| Outbox transacional | O comando é gravado na mesma transação da mudança de estado e publicado depois: nunca se perde |
| Registro de mensagens processadas | A mesma mensagem entregue duas vezes produz efeito uma vez |
| Trava na linha da Saga | Duas confirmações da mesma Saga em réplicas diferentes não se sobrepõem |
| Reenvio de etapa parada | Comando sem resposta há 120 s é reenviado; o participante é idempotente por OS |
| Retry com atraso e DLQ | Falha técnica: 3 novas tentativas a cada 5 s, depois a fila `os.saga-events.dlq` |
| Bloqueio semântico | Enquanto a OS está `EM_CANCELAMENTO`, nenhuma operação de negócio a aceita |

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

Como uma OS vira **um trace só** entre os serviços:

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

O painel **AutoGiro — Saga e rastreamento** e o alerta de mensagem na DLQ ficam no
[autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s), em Terraform.

## Banco de dados

PostgreSQL próprio, num projeto Neon só do serviço (`terraform/`). O schema é versionado em
`migrations/` e aplicado por `python -m app.infrastructure.migrations`, que roda cada arquivo
uma única vez, em ordem.

| Migration | Conteúdo |
|---|---|
| `001_schema_inicial.sql` | Clientes, veículos, catálogo, ordens de serviço e histórico de status |
| `002_contrato_autenticacao.sql` | View `auth.clients` e usuário somente leitura para a Lambda de autenticação |
| `003_dados_de_demonstracao.sql` | Clientes, veículos e serviços de exemplo |
| `004_saga.sql` | Estado das Sagas, outbox e mensagens processadas |

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

A `main` é protegida: só recebe código por pull request, com as três checagens verdes.

## Qualidade e cobertura

- **Painel do SonarCloud:** <https://sonarcloud.io/summary/overall?id=Figueiraa_autogiro-os-service> (quality gate, cobertura, bugs, vulnerabilidades e code smells).
- **Relatório de cada execução:** aba *Summary* da pipeline no GitHub Actions, com o quadro de testes e cobertura, e o artefato `test-and-coverage-reports` (JUnit, `coverage.xml` e HTML).

## Repositórios da plataforma

| Repositório | Papel |
|---|---|
| [autogiro-os-service](https://github.com/Figueiraa/autogiro-os-service) | Atendimento e orquestração da Saga |
| [autogiro-billing-service](https://github.com/Figueiraa/autogiro-billing-service) | Orçamento, pagamento (Mercado Pago) e estorno |
| [autogiro-execution-service](https://github.com/Figueiraa/autogiro-execution-service) | Estoque de peças e fila de execução |
| [autogiro-auth](https://github.com/Figueiraa/autogiro-auth) | Lambda de autenticação por CPF |
| [autogiro-infra-k8s](https://github.com/Figueiraa/autogiro-infra-k8s) | Cluster EKS, Kong, RabbitMQ e New Relic |
