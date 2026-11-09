# ADR-005: Divisão em três microsserviços

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira
- **Substitui:** a decisão anterior de organizar o projeto em quatro repositórios em torno de
  uma aplicação única

## Contexto

A oficina passou a operar em escala nacional. O enunciado da Fase 4 exige **no mínimo 3
microsserviços**, cada um com repositório, infraestrutura e banco próprios, e cita quatro
áreas que interagem: **atendimento, diagnóstico, execução e cobrança**. Na Fase 3, todo o
domínio estava num monólito (`autogiro-app`), com 8 tabelas num único banco.

## Decisão

Três serviços, cada um alinhado a uma área de negócio com linguagem e ritmo próprios:

| Serviço | Área | Responsabilidade |
|---|---|---|
| `autogiro-os-service` | Atendimento | Clientes, veículos, catálogo de serviços, abertura e ciclo de vida da OS, histórico, **orquestração da Saga** |
| `autogiro-billing-service` | Cobrança | Orçamento, aprovação, pagamento (Mercado Pago), estorno |
| `autogiro-execution-service` | Diagnóstico e execução | **Estoque de peças**, reservas, fila de execução, diagnóstico e reparo |

O **estoque fica com a Execução** porque quem consome peças é a oficina, não o atendimento.
Isso cria uma etapa compensável natural na Saga (reservar ↔ liberar) e evita separar a mesma
peça para duas OS (ver [saga.md](../arquitetura/saga.md)).

Os **cadastros** (clientes, veículos, catálogo) ficam no OS Service, porque é o atendimento
que os mantém e a OS depende deles a cada abertura.

Repositórios: os 3 serviços são **novos**, com o código de partida copiado do
`autogiro-app`. O `autogiro-auth` (Lambda) e o `autogiro-infra-k8s` (plataforma) continuam.
O `autogiro-app` e o `autogiro-infra-db` ficam marcados com a tag `v3.0-tc3`, a versão
avaliada na Fase 3.

## Consequências

**Positivas**
- Cada serviço muda e é implantado sem coordenar com os outros.
- Uma falha no Billing (por exemplo, o Mercado Pago fora do ar) não impede abrir OS nem
  operar a fila de execução.
- Os limites coincidem com as áreas citadas no enunciado, o que simplifica a explicação da
  divisão.

**Negativas**
- Dados passam a ser copiados entre serviços (nome e preço de peça, total do orçamento).
  As cópias são instantâneas do momento da mensagem ([posse-dos-dados.md](../arquitetura/posse-dos-dados.md)).
- Três pipelines, três análises no SonarCloud e três bancos para manter.
- Consultas que cruzam serviços passam a ser compostas por cópias, não por JOIN.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| 4º serviço só de cadastros | Mais um repositório, pipeline e banco sem ganho de negócio: cadastros mudam pouco e só o atendimento os mantém |
| Estoque no OS Service | A Execução ficaria só com a fila, e a Saga perderia a etapa de reserva, que é a compensação mais intuitiva de mostrar |
| Serviço separado de diagnóstico | Diagnóstico e reparo são feitos pelo mesmo mecânico, em sequência, sobre o mesmo veículo. Separar criaria uma Saga dentro da execução sem benefício |
