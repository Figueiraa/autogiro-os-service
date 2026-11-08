from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SERVICE_NAME: str = "os-service"
    SERVICE_VERSION: str = "0.1.0"

    # Segredo HS256 compartilhado com a Lambda `autogiro-auth` (emissor) e com o
    # Kong (validador). O serviço revalida a assinatura em vez de confiar no
    # gateway: chamadas internas e a execução local não passam pelo Kong (ADR-004).
    # Obrigatório e sem valor padrão: o serviço não sobe sem ele, e nenhum segredo
    # fica escrito no código.
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "autogiro-auth"

    # Banco próprio do serviço. Em produção, o PostgreSQL do Neon (driver asyncpg);
    # localmente, o PostgreSQL do docker-compose.
    DATABASE_URL: str = "sqlite+aiosqlite:///./os-service.db"

    # Consulta síncrona ao catálogo de peças do Execution Service na abertura da OS.
    # Sem URL configurada, a validação é pulada e fica a cargo da Saga.
    EXECUTION_SERVICE_URL: str | None = None
    EXECUTION_SERVICE_TIMEOUT_SECONDS: float = 2.0

    # Mensageria (RabbitMQ). Sem URL, a API sobe sem consumir nem publicar mensagens:
    # os comandos ficam no outbox até o broker ser configurado.
    RABBITMQ_URL: str | None = None
    # Comando sem resposta há mais que isto é reenviado (o participante é idempotente).
    SAGA_STEP_RETRY_SECONDS: int = 120
    SAGA_STALL_CHECK_SECONDS: float = 30.0
    OUTBOX_POLL_SECONDS: float = 1.0
    # Falha técnica ao processar: tentativas com atraso antes de ir para a DLQ.
    MESSAGE_MAX_RETRIES: int = 3
    MESSAGE_RETRY_DELAY_MS: int = 5000

    # Notificações de mudança de status da OS.
    # auto  -> e-mail se SMTP_HOST estiver definido, senão log
    # email -> força o canal de e-mail (SMTP)
    # log   -> força o canal de log estruturado
    NOTIFICATION_CHANNEL: str = "auto"
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str = "no-reply@autogiro.com.br"
    SMTP_USE_TLS: bool = True
    SMTP_TIMEOUT: int = 10

    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"  # lista separada por vírgula; use origens explícitas em produção

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
