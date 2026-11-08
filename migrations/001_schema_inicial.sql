-- Schema do OS Service: cadastros (clientes, veículos, catálogo de serviços) e
-- ordens de serviço com histórico de status.
--
-- Peças e estoque pertencem ao Execution Service; orçamento e pagamento, ao
-- Billing Service. Aqui ficam só o que foi pedido (part_id) e as cópias que a
-- Saga traz de volta (nome e preço da peça, total do orçamento, link de pagamento),
-- sem chave estrangeira para dados de outro serviço.

CREATE TABLE IF NOT EXISTS clients (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    cpf_cnpj    VARCHAR(18)  NOT NULL,
    phone       VARCHAR(20),
    email       VARCHAR(255),
    address     VARCHAR(500),
    -- Lido pela Lambda autogiro-auth: FALSE bloqueia a emissão de token.
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_clients_cpf_cnpj UNIQUE (cpf_cnpj),
    CONSTRAINT ck_clients_cpf_cnpj_digitos CHECK (cpf_cnpj ~ '^[0-9]{11}$|^[0-9]{14}$')
);

CREATE TABLE IF NOT EXISTS vehicles (
    id          SERIAL PRIMARY KEY,
    plate       VARCHAR(10)  NOT NULL,
    brand       VARCHAR(100) NOT NULL,
    model       VARCHAR(100) NOT NULL,
    year        INTEGER      NOT NULL,
    client_id   INTEGER      NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_vehicles_plate UNIQUE (plate),
    CONSTRAINT ck_vehicles_year CHECK (year BETWEEN 1900 AND 2100)
);

CREATE TABLE IF NOT EXISTS service_types (
    id                          SERIAL PRIMARY KEY,
    name                        VARCHAR(255)   NOT NULL,
    description                 TEXT,
    price                       NUMERIC(10, 2) NOT NULL,
    estimated_duration_minutes  INTEGER        NOT NULL DEFAULT 60,
    created_at                  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    CONSTRAINT uq_service_types_name UNIQUE (name),
    CONSTRAINT ck_service_types_price CHECK (price >= 0),
    CONSTRAINT ck_service_types_duration CHECK (estimated_duration_minutes > 0)
);

CREATE TABLE IF NOT EXISTS service_orders (
    id             SERIAL PRIMARY KEY,
    number         VARCHAR(20)    NOT NULL,
    vehicle_id     INTEGER        NOT NULL REFERENCES vehicles (id) ON DELETE RESTRICT,
    client_id      INTEGER        NOT NULL REFERENCES clients (id) ON DELETE RESTRICT,
    status         VARCHAR(30)    NOT NULL DEFAULT 'RECEBIDA',
    notes          TEXT,
    budget_total   NUMERIC(10, 2),
    payment_link   VARCHAR(500),
    cancel_reason  VARCHAR(40),
    created_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
    started_at     TIMESTAMPTZ,
    completed_at   TIMESTAMPTZ,
    delivered_at   TIMESTAMPTZ,
    CONSTRAINT uq_service_orders_number UNIQUE (number),
    CONSTRAINT ck_service_orders_status CHECK (status IN (
        'RECEBIDA', 'AGUARDANDO_APROVACAO', 'AGUARDANDO_PAGAMENTO', 'PAGA', 'NA_FILA',
        'EM_DIAGNOSTICO', 'EM_EXECUCAO', 'FINALIZADA', 'ENTREGUE', 'EM_CANCELAMENTO', 'CANCELADA'
    )),
    CONSTRAINT ck_service_orders_budget_total CHECK (budget_total IS NULL OR budget_total >= 0),
    CONSTRAINT ck_service_orders_completed_after_started
        CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
);

CREATE TABLE IF NOT EXISTS service_order_items (
    id                SERIAL PRIMARY KEY,
    service_order_id  INTEGER        NOT NULL REFERENCES service_orders (id) ON DELETE CASCADE,
    service_type_id   INTEGER        NOT NULL REFERENCES service_types (id) ON DELETE RESTRICT,
    name              VARCHAR(255)   NOT NULL,
    quantity          INTEGER        NOT NULL DEFAULT 1,
    unit_price        NUMERIC(10, 2) NOT NULL,
    CONSTRAINT ck_soi_quantity CHECK (quantity > 0),
    CONSTRAINT ck_soi_unit_price CHECK (unit_price >= 0)
);

-- part_id é o id da peça no Execution Service: sem chave estrangeira.
CREATE TABLE IF NOT EXISTS service_order_parts (
    id                SERIAL PRIMARY KEY,
    service_order_id  INTEGER        NOT NULL REFERENCES service_orders (id) ON DELETE CASCADE,
    part_id           INTEGER        NOT NULL,
    quantity          INTEGER        NOT NULL DEFAULT 1,
    part_name         VARCHAR(255),
    unit_price        NUMERIC(10, 2),
    CONSTRAINT ck_sop_quantity CHECK (quantity > 0),
    CONSTRAINT ck_sop_unit_price CHECK (unit_price IS NULL OR unit_price >= 0)
);

CREATE TABLE IF NOT EXISTS service_order_status_history (
    id                SERIAL PRIMARY KEY,
    service_order_id  INTEGER      NOT NULL REFERENCES service_orders (id) ON DELETE CASCADE,
    from_status       VARCHAR(30),
    to_status         VARCHAR(30)  NOT NULL,
    reason            VARCHAR(40),
    source            VARCHAR(60)  NOT NULL,
    occurred_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_vehicles_client ON vehicles (client_id);
CREATE INDEX IF NOT EXISTS ix_service_orders_status ON service_orders (status);
CREATE INDEX IF NOT EXISTS ix_service_orders_vehicle ON service_orders (vehicle_id);
CREATE INDEX IF NOT EXISTS ix_soi_service_order ON service_order_items (service_order_id);
CREATE INDEX IF NOT EXISTS ix_sop_service_order ON service_order_parts (service_order_id);
CREATE INDEX IF NOT EXISTS ix_history_service_order ON service_order_status_history (service_order_id);
CREATE INDEX IF NOT EXISTS ix_clients_ativos ON clients (cpf_cnpj) WHERE is_active;
