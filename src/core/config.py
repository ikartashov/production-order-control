from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Централизованная конфигурация всего приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    # Приложение
    app_title: str = "Production Control API"
    app_version: str = "0.1.0"
    debug: bool = False

    # PostgreSQL
    database_url: PostgresDsn = Field(
        ...,
        description="Async DSN, например: postgresql+asyncpg://user:pass@host:5432/db",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # Redis
    redis_url: RedisDsn = Field(..., description="например: redis://localhost:6379/0")
    redis_cache_db: int = 0
    redis_celery_db: int = 1

    # Celery / RabbitMQ
    celery_broker_url: str = Field(
        ..., description="AMQP URL, например: amqp://admin:admin@localhost:5672//"
    )
    celery_result_backend: str = Field(
        ..., description="Redis URL для хранения результатов Celery"
    )

    # MinIO
    minio_endpoint: str = Field(..., description="host:port, например: localhost:9000")
    minio_access_key: str
    minio_secret_key: str
    minio_secure: bool = False

    # Безопасность
    secret_key: str = Field(
        ..., min_length=32, description="Используется для HMAC-подписи"
    )

    # Rate limiting
    rate_limit_requests: int = Field(
        default=100, description="Максимум запросов на клиента за окно"
    )
    rate_limit_window_seconds: int = Field(
        default=60, description="Длительность окна rate limiting, секунды"
    )
    rate_limit_exempt_paths: list[str] = Field(
        default=["/health"],
        description=(
            "Пути, исключённые из rate limiting. Парсится pydantic-settings "
            "из переменной окружения как JSON-массив строк, например: "
            'RATE_LIMIT_EXEMPT_PATHS=["/health","/docs"]'
        ),
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        """Гарантирует, что DSN использует драйвер asyncpg."""
        if isinstance(v, str) and "postgresql://" in v and "asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


def get_settings() -> Settings:
    """Возвращает кешированный экземпляр Settings.

    Импортируйте и вызывайте эту функцию везде, а не импортируйте ``Settings``
    напрямую — это держит конфигурацию в одном месте и упрощает мокирование в тестах.
    """
    return _settings


_settings = Settings()
