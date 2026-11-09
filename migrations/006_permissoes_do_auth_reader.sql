-- Permissões do usuário da Lambda de autenticação, reaplicadas.
--
-- No Neon o papel auth_reader passou a ser criado pelo usuário do serviço (via
-- Terraform), e não pela API do Neon: papel criado pela API entra no grupo
-- neon_superuser e lê todas as tabelas. Um papel novo não herda as permissões
-- dadas pela migration 002 ao anterior, então elas são dadas de novo aqui.
-- Tudo é idempotente: localmente e no CI o papel é o da migration 002.

GRANT USAGE ON SCHEMA auth TO auth_reader;
GRANT SELECT ON auth.clients TO auth_reader;

DO $$
BEGIN
    ALTER ROLE auth_reader SET search_path = auth;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'auth_reader criado fora do serviço: search_path definido na URL de conexão';
END
$$;
