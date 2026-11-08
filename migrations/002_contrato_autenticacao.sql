-- Contrato de leitura para a Lambda autogiro-auth (ADR-013).
--
-- A Lambda autentica o cliente pelo CPF e precisa de 4 colunas de `clients`.
-- Em vez de ler a tabela, ela lê uma view num schema próprio, com um usuário que
-- só enxerga essa view: nada de veículos, ordens de serviço, e-mail, telefone ou
-- endereço. O OS Service pode mudar `clients` à vontade, desde que mantenha a view.
--
-- O `search_path` do usuário aponta para o schema `auth`, então a consulta atual da
-- Lambda (`FROM clients`) lê a view sem mudar o código dela.

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
ALTER ROLE auth_reader SET search_path = auth;
