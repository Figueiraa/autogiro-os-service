-- Contexto do trace (cabeçalhos W3C) em que cada mensagem do outbox foi criada.
-- O publicador o envia junto com a mensagem, para o trace continuar no serviço
-- que a consome.

ALTER TABLE outbox ADD COLUMN IF NOT EXISTS trace_context TEXT;
