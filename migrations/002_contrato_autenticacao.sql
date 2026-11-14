-- Contrato de leitura para a Lambda autogiro-auth (ADR-013).
--
-- A Lambda autentica o cliente pelo CPF e precisa de 4 colunas de `clients`.
-- Em vez de ler a tabela, ela lê uma view num schema próprio, com um usuário que
-- só enxerga essa view: nada de veículos, ordens de serviço, e-mail, telefone ou
-- endereço. O OS Service pode mudar `clients` à vontade, desde que mantenha a view.
--
-- O `search_path` do usuário aponta para o schema `auth`, então a consulta atual da
-- Lambda (`FROM clients`) lê a view sem mudar o código dela. No Neon o usuário é
-- criado pelo terraform/ (`postgresql_role`, com o usuário do serviço) já com esse
-- `search_path`, que também vai na URL de conexão (saída `auth_database_url`) como
-- garantia. A migration 006 reaplica as permissões a esse papel.

CREATE SCHEMA IF NOT EXISTS auth;

CREATE OR REPLACE VIEW auth.clients AS
    SELECT id, name, cpf_cnpj, is_active
    FROM public.clients;

-- No Neon o usuário é criado pela Terraform (com senha); localmente e no CI ele
-- ainda não existe, então é criado aqui sem login.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_reader') THEN
        CREATE ROLE auth_reader NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA auth TO auth_reader;
GRANT SELECT ON auth.clients TO auth_reader;
-- Só quem tem ADMIN sobre o papel pode alterá-lo (PostgreSQL 16+). Localmente e no
-- CI o papel acabou de ser criado aqui; no Neon, quem o cria é o terraform/, e o
-- search_path também vem da URL.
DO $$
BEGIN
    ALTER ROLE auth_reader SET search_path = auth;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'auth_reader criado fora das migrations: search_path definido na URL de conexão';
END
$$;
