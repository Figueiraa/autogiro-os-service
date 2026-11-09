# ADR-006: Um banco por serviço — PostgreSQL e MongoDB

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira

## Contexto

O enunciado exige banco próprio por serviço, pelo menos um relacional e pelo menos um não
relacional, e proíbe um serviço de acessar o banco de outro. Na Fase 3 usávamos um único
PostgreSQL no Neon.

## Decisão

| Serviço | Banco | Hospedagem | Por quê |
|---|---|---|---|
| OS Service | **PostgreSQL 17** | Neon, projeto `autogiro-os` (banco `autogiro_os`) | Cadastros são relacionais (cliente → veículo → OS), com FKs internas. O esquema vem da Fase 3 |
| Billing Service | **PostgreSQL 17** | Neon, projeto `autogiro-billing` (banco `autogiro_billing`) | É dinheiro: aprovar o orçamento e criar o pagamento precisam acontecer numa transação ACID |
| Execution Service | **MongoDB** | Atlas M0 | A execução é um documento que cresce: a linha do tempo de diagnóstico e reparo tem tamanho e conteúdo variáveis. A reserva de várias peças usa transação multi-documento (o M0 é um replica set) |

- Cada banco tem **credenciais próprias**, entregues só ao seu serviço por Secret do
  Kubernetes (`os-service-secret`, e equivalentes nos outros dois). Nenhum serviço recebe a
  credencial de outro.
- Os dois projetos Neon são criados pela Terraform do próprio repositório do serviço
  (`terraform/`, provider `kislerdm/neon`), que também gera a `DATABASE_URL`. O cluster Atlas
  foi criado pelo painel do Atlas e está documentado no README do Execution, sem Terraform:
  uma simplificação consciente, já que é um cluster gratuito criado uma vez.
- Custo: Neon e Atlas M0 têm plano gratuito permanente.

## Consequências

**Positivas**
- Atende à exigência de um banco relacional e um não relacional com uma justificativa de
  negócio para cada escolha, e não só para cumprir o requisito.
- Isolamento de falhas e de carga: um banco lento afeta só o seu serviço.
- Os bancos ficam fora do cluster e sobrevivem à sua recriação.

**Negativas**
- Sem JOIN entre serviços: o que precisa ser exibido junto é copiado por mensagem.
- Três conexões, três strings de conexão e três políticas de backup diferentes.
- Localmente, o MongoDB precisa subir como replica set (`--replSet rs0`) para aceitar
  transações.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| DynamoDB no Execution | Exige IAM e IRSA numa conta AWS que já mostrou bloqueios (sem Load Balancer, sem invocação pública de Function URL). Sem aula no módulo |
| MongoDB no Billing | Perde a transação ACID entre aprovação e criação do pagamento |
| Redis como banco do Execution | É chave-valor em memória: bom para cache e filas efêmeras, ruim como fonte de verdade de estoque |
| Bancos dentro do cluster | O cluster é destruído ao fim de cada sessão (custo). O banco precisa sobreviver ao cluster |
