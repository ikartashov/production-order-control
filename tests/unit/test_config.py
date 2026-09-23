import pytest
from pydantic import ValidationError

from core.config import Settings

# Переменные, влияющие на составные/дискретные поля Settings — чистим их из
# окружения процесса перед каждым тестом в этом модуле. `_env_file=None` в
# `_make_settings` изолирует тесты только от реального `.env` в корне
# репозитория, но не от переменных, уже выставленных в самом окружении
# процесса (например, DATABASE_URL/REDIS_URL, которые нужны при запуске
# `pytest tests/integration/` вне Docker — см. README) — BaseSettings читает
# os.environ независимо от env_file, так что без явной очистки тесты этого
# модуля были бы недетерминированы в зависимости от того, чем их запустили.
_ENV_VARS_TO_ISOLATE = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB_CACHE",
    "REDIS_DB_CELERY",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "RABBITMQ_HOST",
    "RABBITMQ_PORT",
    "RABBITMQ_USER",
    "RABBITMQ_PASSWORD",
    "RABBITMQ_VHOST",
    "RATE_LIMIT_REQUESTS",
    "RATE_LIMIT_WINDOW_SECONDS",
    "RATE_LIMIT_EXEMPT_PATHS",
)


@pytest.fixture(autouse=True)
def _isolate_from_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убирает из окружения процесса переменные, которые иначе перекрыли бы
    kwargs, явно переданные в тестах этого модуля (см. комментарий выше)."""
    for var in _ENV_VARS_TO_ISOLATE:
        monkeypatch.delenv(var, raising=False)


# Минимальный набор полей без discrete/composed duality (обязательны всегда),
# используется во всех тестах ниже, чтобы не зависеть от реального .env
# в корне репозитория.
_BASE_REQUIRED_KWARGS: dict = {
    "minio_endpoint": "localhost:9000",
    "minio_access_key": "minioadmin",
    "minio_secret_key": "minioadmin",
    "secret_key": "x" * 32,
}


def _make_settings(**kwargs: object) -> Settings:
    """Строит ``Settings`` в изоляции от реального ``.env`` в корне репозитория."""
    return Settings(_env_file=None, **{**_BASE_REQUIRED_KWARGS, **kwargs})  # type: ignore[call-arg, arg-type]


# Полный валидный набор дискретных полей для всех трёх бэкендов (Postgres/
# Redis/RabbitMQ) — используется как база в тестах, которые проверяют сборку
# только одного конкретного URL и не хотят падать из-за нехватки полей
# для остальных.
_ALL_DISCRETE_KWARGS: dict = {
    "postgres_host": "db.internal",
    "postgres_port": 5433,
    "postgres_db": "production_control",
    "postgres_user": "app",
    "postgres_password": "s3cret",
    "redis_host": "cache.internal",
    "redis_port": 6380,
    "rabbitmq_host": "mq.internal",
    "rabbitmq_port": 5673,
    "rabbitmq_user": "admin",
    "rabbitmq_password": "admin",
}


class TestRateLimitSettings:
    """Тесты дефолтных значений полей rate limiting в ``Settings``."""

    def test_defaults_when_not_overridden_by_env(self) -> None:
        """Без переопределения через окружение используются дефолты.

        Строится через ``_make_settings``/``_ALL_DISCRETE_KWARGS`` (а не
        голым ``Settings()``), чтобы тест не зависел от того, есть ли в
        окружении, где он запускается, реальный ``.env`` или явно заданные
        DATABASE_URL/POSTGRES_*/... — раньше это делало тест недетерминиро-
        ванным: локально (где есть гитигнорящийся ``.env`` с готовым
        DATABASE_URL) он проходил, а в CI-джобе без этих переменных падал
        на сборке DATABASE_URL, хотя сам тест вообще не про DATABASE_URL.
        """
        settings = _make_settings(**_ALL_DISCRETE_KWARGS)

        assert settings.rate_limit_requests == 100
        assert settings.rate_limit_window_seconds == 60
        assert settings.rate_limit_exempt_paths == ["/health"]


class TestDatabaseUrlAssembly:
    """Сборка DATABASE_URL из дискретных полей POSTGRES_*."""

    def test_assembled_from_discrete_fields(self) -> None:
        settings = _make_settings(**_ALL_DISCRETE_KWARGS)

        assert str(settings.database_url) == (
            "postgresql+asyncpg://app:s3cret@db.internal:5433/production_control"
        )

    def test_explicit_database_url_is_not_overridden(self) -> None:
        settings = _make_settings(
            **_ALL_DISCRETE_KWARGS,
            database_url="postgresql+asyncpg://explicit:pass@explicit-host:5432/explicit_db",
        )

        assert str(settings.database_url) == (
            "postgresql+asyncpg://explicit:pass@explicit-host:5432/explicit_db"
        )

    def test_missing_url_and_incomplete_discrete_fields_raises(self) -> None:
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            _make_settings(postgres_host="db.internal")


class TestRedisUrlAssembly:
    """Сборка REDIS_URL и CELERY_RESULT_BACKEND из дискретных полей REDIS_*."""

    def test_redis_url_assembled_from_cache_db(self) -> None:
        settings = _make_settings(
            postgres_host="db",
            postgres_db="d",
            postgres_user="u",
            postgres_password="p",
            redis_host="cache.internal",
            redis_port=6380,
            redis_cache_db=3,
            rabbitmq_host="mq.internal",
            rabbitmq_user="admin",
            rabbitmq_password="admin",
        )

        assert str(settings.redis_url) == "redis://cache.internal:6380/3"

    def test_celery_result_backend_assembled_from_celery_db(self) -> None:
        settings = _make_settings(
            postgres_host="db",
            postgres_db="d",
            postgres_user="u",
            postgres_password="p",
            redis_host="cache.internal",
            redis_port=6380,
            redis_celery_db=5,
            rabbitmq_host="mq.internal",
            rabbitmq_user="admin",
            rabbitmq_password="admin",
        )

        assert settings.celery_result_backend == "redis://cache.internal:6380/5"

    def test_missing_redis_url_and_host_raises(self) -> None:
        with pytest.raises(ValidationError, match="REDIS_URL"):
            _make_settings(
                postgres_host="db",
                postgres_db="d",
                postgres_user="u",
                postgres_password="p",
            )


class TestCeleryBrokerUrlAssembly:
    """Сборка CELERY_BROKER_URL (AMQP) из дискретных полей RABBITMQ_*."""

    def test_assembled_with_default_root_vhost_encoded(self) -> None:
        settings = _make_settings(
            postgres_host="db",
            postgres_db="d",
            postgres_user="u",
            postgres_password="p",
            redis_host="cache",
            rabbitmq_host="mq.internal",
            rabbitmq_port=5673,
            rabbitmq_user="admin",
            rabbitmq_password="admin",
            rabbitmq_vhost="/",
        )

        assert settings.celery_broker_url == "amqp://admin:admin@mq.internal:5673/%2F"

    def test_assembled_with_custom_vhost(self) -> None:
        settings = _make_settings(
            postgres_host="db",
            postgres_db="d",
            postgres_user="u",
            postgres_password="p",
            redis_host="cache",
            rabbitmq_host="mq.internal",
            rabbitmq_user="admin",
            rabbitmq_password="admin",
            rabbitmq_vhost="myvhost",
        )

        assert (
            settings.celery_broker_url == "amqp://admin:admin@mq.internal:5672/myvhost"
        )

    def test_explicit_celery_broker_url_is_not_overridden(self) -> None:
        settings = _make_settings(
            database_url="postgresql+asyncpg://u:p@h:5432/d",
            redis_url="redis://h:6379/0",
            celery_result_backend="redis://h:6379/1",
            celery_broker_url="amqp://explicit:explicit@explicit-host:5672/%2F",
            rabbitmq_host="mq.internal",
            rabbitmq_user="admin",
            rabbitmq_password="admin",
        )

        assert (
            settings.celery_broker_url
            == "amqp://explicit:explicit@explicit-host:5672/%2F"
        )

    def test_missing_broker_url_and_incomplete_discrete_fields_raises(self) -> None:
        with pytest.raises(ValidationError, match="CELERY_BROKER_URL"):
            _make_settings(
                postgres_host="db",
                postgres_db="d",
                postgres_user="u",
                postgres_password="p",
                redis_host="cache",
                rabbitmq_host="mq.internal",
                # rabbitmq_user/rabbitmq_password намеренно отсутствуют
            )


class TestSpecialCharactersAreUrlEncoded:
    """Регрессия: пароль/логин со спецсимволами (`@`, `/`, `:`) должны быть
    URL-закодированы при сборке URL, иначе DSN-парсер молча обрезает/путает
    хост и пароль вместо явной ошибки конфигурации."""

    def test_database_url_encodes_special_characters_in_credentials(self) -> None:
        settings = _make_settings(
            postgres_host="db",
            postgres_db="d",
            postgres_user="u@ser",
            postgres_password="p@ss/word:1",
            redis_host="cache",
            rabbitmq_host="mq",
            rabbitmq_user="admin",
            rabbitmq_password="admin",
        )

        # PostgresDsn хранит/отдаёт компоненты URL уже в закодированном виде
        # (как они будут физически присутствовать в строке подключения) —
        # именно поэтому исходный "@"/"/" не проглатывают хост/базу при
        # разборе клиентом.
        hosts = settings.database_url.hosts()[0]  # type: ignore[union-attr]
        assert hosts["username"] == "u%40ser"
        assert hosts["password"] == "p%40ss%2Fword%3A1"
        assert hosts["host"] == "db"

    def test_celery_broker_url_encodes_special_characters_in_credentials(
        self,
    ) -> None:
        settings = _make_settings(
            postgres_host="db",
            postgres_db="d",
            postgres_user="u",
            postgres_password="p",
            redis_host="cache",
            rabbitmq_host="mq.internal",
            rabbitmq_user="admin",
            rabbitmq_password="ad@min:pw/x",
        )

        assert settings.celery_broker_url == (
            "amqp://admin:ad%40min%3Apw%2Fx@mq.internal:5672/%2F"
        )


class TestRedisDbAliasesMatchDocumentedEnvVars:
    """Регрессия: .env.example и Field-описания документируют
    REDIS_DB_CACHE/REDIS_DB_CELERY как имена переменных окружения — без
    явного alias pydantic-settings матчил бы поля redis_cache_db/
    redis_celery_db на REDIS_CACHE_DB/REDIS_CELERY_DB (по умолчанию, из
    upper-case имени поля) и молча игнорировал бы задокументированные
    имена."""

    def test_redis_db_cache_env_var_is_honored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDIS_DB_CACHE", "7")
        settings = _make_settings(**_ALL_DISCRETE_KWARGS)

        assert settings.redis_cache_db == 7
        assert str(settings.redis_url) == "redis://cache.internal:6380/7"

    def test_redis_db_celery_env_var_is_honored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDIS_DB_CELERY", "9")
        settings = _make_settings(**_ALL_DISCRETE_KWARGS)

        assert settings.redis_celery_db == 9
        assert settings.celery_result_backend == "redis://cache.internal:6380/9"
