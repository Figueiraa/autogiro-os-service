"""Migrations contra um PostgreSQL de verdade.

Roda quando `TEST_POSTGRES_URL` está definida (no CI, um container do PostgreSQL;
localmente, o banco do docker-compose). Sem ela, os testes são pulados.
"""

import os

import asyncpg
import pytest

from app.infrastructure.migrations import migrate, to_asyncpg_dsn

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL não definida")


@pytest.fixture
async def banco_limpo():
    conn = await asyncpg.connect(to_asyncpg_dsn(POSTGRES_URL))
    await conn.execute("DROP SCHEMA IF EXISTS auth CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    yield conn
    await conn.close()


async def test_aplica_tudo_uma_vez_e_e_idempotente(banco_limpo):
    primeira = await migrate(POSTGRES_URL)
    segunda = await migrate(POSTGRES_URL)

    assert primeira == [
        "001_schema_inicial.sql",
        "002_contrato_autenticacao.sql",
        "003_dados_de_demonstracao.sql",
        "004_saga.sql",
        "005_contexto_de_rastreamento.sql",
    ]
    assert segunda == []
    colunas = {
        row["column_name"]
        for row in await banco_limpo.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'outbox'"
        )
    }
    assert "trace_context" in colunas


async def test_cria_as_tabelas_e_os_dados_de_demonstracao(banco_limpo):
    await migrate(POSTGRES_URL)

    tabelas = {
        row["table_name"]
        for row in await banco_limpo.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {
        "clients",
        "vehicles",
        "service_types",
        "service_orders",
        "service_order_items",
        "service_order_parts",
        "service_order_status_history",
        "schema_migrations",
    } <= tabelas
    assert "parts" not in tabelas  # peças pertencem ao Execution Service
    assert await banco_limpo.fetchval("SELECT count(*) FROM clients") == 5
    assert await banco_limpo.fetchval("SELECT count(*) FROM clients WHERE NOT is_active") == 1


async def test_status_fora_da_maquina_de_estados_e_rejeitado(banco_limpo):
    await migrate(POSTGRES_URL)

    with pytest.raises(asyncpg.CheckViolationError):
        await banco_limpo.execute(
            "INSERT INTO service_orders (number, vehicle_id, client_id, status) "
            "SELECT 'OSX', v.id, v.client_id, 'INVENTADO' FROM vehicles v LIMIT 1"
        )


async def test_usuario_da_lambda_so_enxerga_a_view_de_autenticacao(banco_limpo):
    await migrate(POSTGRES_URL)

    async with banco_limpo.transaction():
        await banco_limpo.execute("SET LOCAL ROLE auth_reader")
        await banco_limpo.execute("SET LOCAL search_path = auth")

        # A consulta da Lambda, sem alteração, lê a view.
        cliente = await banco_limpo.fetchrow(
            "SELECT id, name, cpf_cnpj, is_active FROM clients WHERE cpf_cnpj = $1", "44232322191"
        )
        assert cliente["name"] == "Maria Oliveira"

    async with banco_limpo.transaction():
        await banco_limpo.execute("SET LOCAL ROLE auth_reader")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await banco_limpo.fetch("SELECT email FROM public.clients")
