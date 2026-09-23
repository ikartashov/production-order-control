from urllib.parse import quote

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Централизованная конфигурация всего приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Позволяет строить Settings и по имени поля (redis_cache_db=...,
        # используется в тестах/прямом конструировании), и по validation_alias
        # (REDIS_DB_CACHE из окружения) — без этого прямая передача по имени
        # поля перестала бы работать для полей с явным alias.
        populate_by_name=True,
    )
    # Приложение
    app_title: str = "Production Control API"
    app_version: str = "0.1.0"
    debug: bool = False

    # PostgreSQL
    database_url: PostgresDsn | None = Field(
        default=None,
        description="Async DSN, например: postgresql+asyncpg://user:pass@host:5432/db. "
        "Если не указан явно, собирается из POSTGRES_HOST/POSTGRES_PORT/"
        "POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD.",
    )
    postgres_host: str | None = None
    postgres_port: int = 5432
    postgres_db: str | None = None
    postgres_user: str | None = None
    postgres_password: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # Redis
    redis_url: RedisDsn | None = Field(
        default=None,
        description="например: redis://localhost:6379/0. Если не указан явно, "
        "собирается из REDIS_HOST/REDIS_PORT/REDIS_DB_CACHE.",
    )
    redis_host: str | None = None
    redis_port: int = 6379
    # alias, т.к. pydantic-settings по умолчанию матчит поле на
    # REDIS_CACHE_DB/REDIS_CELERY_DB (верхний регистр имени поля), а не на
    # уже задокументированные в .env.example REDIS_DB_CACHE/REDIS_DB_CELERY —
    # без явного alias переменная окружения молча игнорировалась бы.
    redis_cache_db: int = Field(default=0, validation_alias="REDIS_DB_CACHE")
    redis_celery_db: int = Field(default=1, validation_alias="REDIS_DB_CELERY")

    # Celery / RabbitMQ
    celery_broker_url: str | None = Field(
        default=None,
        description="AMQP URL, например: amqp://admin:admin@localhost:5672//. Если не "
        "указан явно, собирается из RABBITMQ_HOST/RABBITMQ_PORT/RABBITMQ_USER/"
        "RABBITMQ_PASSWORD/RABBITMQ_VHOST.",
    )
    celery_result_backend: str | None = Field(
        default=None,
        description="Redis URL для хранения результатов Celery. Если не указан явно, "
        "собирается из REDIS_HOST/REDIS_PORT/REDIS_DB_CELERY.",
    )
    rabbitmq_host: str | None = None
    rabbitmq_port: int = 5672
    rabbitmq_user: str | None = None
    rabbitmq_password: str | None = None
    rabbitmq_vhost: str = "/"

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

    @model_validator(mode="after")
    def _assemble_composed_urls(self) -> "Settings":
        """Собирает DATABASE_URL/REDIS_URL/CELERY_* из дискретных полей.

        Если соответствующая составная переменная (``DATABASE_URL`` и т.п.)
        задана явно в окружении, она имеет приоритет и не переопределяется.
        Иначе URL собирается из дискретных host/port/credential-полей
        (``POSTGRES_HOST`` и т.д., см. ``.env.example``). Если не хватает ни
        явного URL, ни полного набора дискретных полей — падаем с понятной
        ошибкой валидации ещё на старте приложения.
        """
        if self.database_url is None:
            if (
                self.postgres_host
                and self.postgres_db
                and self.postgres_user
                and self.postgres_password
            ):
                self.database_url = PostgresDsn(
                    f"postgresql+asyncpg://{quote(self.postgres_user, safe='')}:"
                    f"{quote(self.postgres_password, safe='')}@{self.postgres_host}:"
                    f"{self.postgres_port}/{self.postgres_db}"
                )
            else:
                raise ValueError(
                    "Укажите DATABASE_URL напрямую, либо полный набор "
                    "POSTGRES_HOST/POSTGRES_DB/POSTGRES_USER/POSTGRES_PASSWORD"
                )

        if self.redis_url is None:
            if self.redis_host:
                self.redis_url = RedisDsn(
                    f"redis://{self.redis_host}:{self.redis_port}/{self.redis_cache_db}"
                )
            else:
                raise ValueError("Укажите REDIS_URL напрямую, либо REDIS_HOST")

        if self.celery_result_backend is None:
            if self.redis_host:
                self.celery_result_backend = f"redis://{self.redis_host}:{self.redis_port}/{self.redis_celery_db}"
            else:
                raise ValueError(
                    "Укажите CELERY_RESULT_BACKEND напрямую, либо REDIS_HOST"
                )

        if self.celery_broker_url is None:
            if self.rabbitmq_host and self.rabbitmq_user and self.rabbitmq_password:
                encoded_vhost = quote(self.rabbitmq_vhost, safe="")
                self.celery_broker_url = (
                    f"amqp://{quote(self.rabbitmq_user, safe='')}:"
                    f"{quote(self.rabbitmq_password, safe='')}@{self.rabbitmq_host}:"
                    f"{self.rabbitmq_port}/{encoded_vhost}"
                )
            else:
                raise ValueError(
                    "Укажите CELERY_BROKER_URL напрямую, либо полный набор "
                    "RABBITMQ_HOST/RABBITMQ_USER/RABBITMQ_PASSWORD"
                )

        return self


def get_settings() -> Settings:
    """Возвращает кешированный экземпляр Settings.

    Импортируйте и вызывайте эту функцию везде, а не импортируйте ``Settings``
    напрямую — это держит конфигурацию в одном месте и упрощает мокирование в тестах.
    """
    return _settings


_settings = Settings()
