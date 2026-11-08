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

## Banco de dados

PostgreSQL próprio, num projeto Neon só do serviço (`terraform/`). O schema é versionado em
`migrations/` e aplicado por `python -m app.infrastructure.migrations`, que roda cada arquivo
uma única vez, em ordem.

| Migration | Conteúdo |
|---|---|
| `001_schema_inicial.sql` | Clientes, veículos, catálogo, ordens de serviço e histórico de status |
| `002_contrato_autenticacao.sql` | View `auth.clients` e usuário somente leitura para a Lambda de autenticação |
| `003_dados_de_demonstracao.sql` | Clientes, veículos e serviços de exemplo |

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

Os testes rodam sobre SQLite em memória. Os testes das migrations precisam de um PostgreSQL
e rodam quando `TEST_POSTGRES_URL` está definida (no CI, um container do PostgreSQL 17):

```bash
TEST_POSTGRES_URL=postgresql+asyncpg://autogiro:autogiro@localhost:5433/autogiro_os pytest
```

O `JWT_SECRET` é obrigatório e não tem valor padrão no código: o serviço não sobe sem ele.

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
