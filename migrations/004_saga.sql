-- Saga orquestrada: estado da Saga de cada OS, outbox de comandos e registro das
-- mensagens já processadas.

CREATE TABLE IF NOT EXISTS saga_instances (
    id              VARCHAR(36)  PRIMARY KEY,
    order_id        INTEGER      NOT NULL REFERENCES service_orders (id),
    state           VARCHAR(30)  NOT NULL,
    current_step    VARCHAR(30),
    failure_reason  VARCHAR(40),
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    CONSTRAINT uq_saga_instances_order UNIQUE (order_id),
    CONSTRAINT ck_saga_instances_state CHECK (state IN ('EM_ANDAMENTO', 'COMPENSANDO', 'CONCLUIDA', 'COMPENSADA'))
);

CREATE TABLE IF NOT EXISTS saga_steps (
    id            SERIAL       PRIMARY KEY,
    saga_id       VARCHAR(36)  NOT NULL REFERENCES saga_instances (id) ON DELETE CASCADE,
    step          VARCHAR(30)  NOT NULL,
    status        VARCHAR(20)  NOT NULL,
    attempts      INTEGER      NOT NULL DEFAULT 0,
    last_sent_at  TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    CONSTRAINT uq_saga_steps UNIQUE (saga_id, step),
    CONSTRAINT ck_saga_steps_status CHECK (status IN ('PENDENTE', 'CONCLUIDA', 'FALHOU'))
);

-- Comandos gravados na mesma transação da mudança de estado e publicados depois.
CREATE TABLE IF NOT EXISTS outbox (
    id            SERIAL        PRIMARY KEY,
    message_id    VARCHAR(36)   NOT NULL,
    message_type  VARCHAR(60)   NOT NULL,
    exchange      VARCHAR(60)   NOT NULL,
    routing_key   VARCHAR(120)  NOT NULL,
    body          TEXT          NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ,
    CONSTRAINT uq_outbox_message UNIQUE (message_id)
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id    VARCHAR(36)  PRIMARY KEY,
    processed_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_saga_instances_running ON saga_instances (state)
    WHERE state IN ('EM_ANDAMENTO', 'COMPENSANDO');
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox (id) WHERE published_at IS NULL;
