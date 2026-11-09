# ADR-013: A Lambda de autenticação pertence ao contexto de Atendimento

- **Status:** Aceita
- **Data:** 2026-11-08
- **Decisores:** Pedro Figueira

## Contexto

A Lambda `autogiro-auth` autentica o cliente pelo CPF: consulta
`SELECT id, name, cpf_cnpj, is_active FROM clients WHERE cpf_cnpj = %s LIMIT 1` e emite o JWT.
Na Fase 3 havia um banco só. Agora **`clients` pertence ao OS Service**, e o enunciado
proíbe, com destaque, um serviço acessar o banco de outro.

## Decisão

1. A Lambda **não é um dos 3 microsserviços**: é o **componente de autenticação do contexto
   de Atendimento**, o mesmo contexto que é dono de `clients`. Ela continua num repositório
   próprio (`autogiro-auth`) porque tem runtime e ciclo de deploy diferentes (serverless na
   AWS).
2. O OS Service publica um **contrato de leitura mínimo** no próprio banco, pela migration
   [`002_contrato_autenticacao.sql`](../../migrations/002_contrato_autenticacao.sql):
   ```sql
   CREATE SCHEMA IF NOT EXISTS auth;
   CREATE OR REPLACE VIEW auth.clients AS
       SELECT id, name, cpf_cnpj, is_active FROM public.clients;
   GRANT USAGE ON SCHEMA auth TO auth_reader;
   GRANT SELECT ON auth.clients TO auth_reader;
   ```
3. O usuário `auth_reader` é criado de formas diferentes conforme o ambiente:
   - **No Neon**, pelo **usuário do próprio serviço**, via Terraform (provider `postgresql`,
     recurso `postgresql_role.auth_reader` em [`terraform/main.tf`](../../terraform/main.tf)),
     com senha aleatória e `search_path = auth`. **Não** pela API do Neon: todo papel criado
     pela API entra no grupo `neon_superuser`, que tem `pg_read_all_data` e lê todas as
     tabelas do banco. Foi o que aconteceu na primeira versão: o `auth_reader` conseguia ler
     veículos e ordens de serviço, e o usuário do serviço não tinha permissão para tirá-lo do
     grupo. Criado pelo usuário do serviço, o papel não herda nada.
   - **Localmente e no CI**, a migration cria o papel (`NOLOGIN`) se ele não existir.

   A migration [`006_permissoes_do_auth_reader.sql`](../../migrations/006_permissoes_do_auth_reader.sql)
   reaplica as permissões (um papel recriado não herda as do anterior). O `search_path=auth`
   também vai na URL de conexão da Lambda (saída `auth_database_url` do
   [`terraform/outputs.tf`](../../terraform/outputs.tf)), como garantia.
4. Com o `search_path` apontando para `auth`, a consulta atual da Lambda (`FROM clients`) lê
   a view `auth.clients`, **sem mudar o código** do `autogiro-auth`; só a variável
   `DATABASE_URL` muda.

O `auth_reader` enxerga só a view: nada de veículos, OS, e-mail, telefone ou endereço do
cliente. Conferido no banco: `has_table_privilege('auth_reader', 'public.service_orders',
'SELECT')` é `false`, e um `SELECT` em `public.vehicles` é negado. E **nenhum dos 3
microsserviços** acessa o banco de outro.

## Consequências

**Positivas**
- Login sem dependência de rede do cluster (que é recriado a cada sessão).
- Menor privilégio: 4 colunas, somente leitura.
- O OS Service controla o contrato: pode mudar a tabela `clients` à vontade, desde que
  mantenha a view.

**Negativas**
- É um acesso a banco **fora** do dono do código. Precisa ficar documentado, porque um leitor
  apressado pode tomá-lo como violação da regra de que nenhum serviço acessa o banco de outro.
- Mudar a view exige coordenar com o `autogiro-auth`.
- O papel da Lambda não pode ser criado pela API do Neon (herdaria leitura de tudo); depende
  do provider `postgresql` do Terraform, conectado como o usuário do serviço.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| A Lambda chamar uma API do OS Service | O Kong é exposto por NodePort no IP de um nó, que muda a cada cluster. Todo login dependeria de uma URL instável |
| Banco próprio da Lambda, sincronizado por eventos | A Lambda fica fora do cluster e não consome o RabbitMQ; exigiria outra ponte (SQS) só para isso |
| Mover a autenticação para dentro do OS Service | Removeria a função serverless exigida e entregue na Fase 3 |
