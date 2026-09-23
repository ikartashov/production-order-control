"""Фикстуры для интеграционных тестов (реальные Postgres + Redis).

В отличие от ``tests/unit`` (репозитории/сервисы полностью замоканы), эти
тесты бьют по настоящей базе данных и настоящему Redis через полный стек
HTTP -> роутер -> сервис -> репозиторий -> БД.

Как это направлено на реальную БД
----------------------------------
``core.database.engine`` создаётся один раз при первом импорте модуля из
``Settings.database_url`` (см. ``core/config.py``, ``core/database.py``).
Вместо того чтобы городить отдельный тестовый ``.env``/переопределение
настроек до первого импорта ``core.config`` (что не только "хрупко" из-за
кеширования импортов Python, но и не нужно — сессия в тесте всё равно
откатывается), тесты используют СТАНДАРТНЫЙ паттерн SQLAlchemy 2.0:
подключение открывается один раз, на нём начинается транзакция, сессия
привязывается к этому соединению (``join_transaction_mode="create_savepoint"``,
чтобы ``session.commit()`` внутри сервисов не завершал внешнюю транзакцию —
он лишь освобождает/переоткрывает SAVEPOINT), а по завершении теста внешняя
транзакция откатывается. Реальные данные в БД, при какую бы DATABASE_URL
она ни была настроена, никогда не изменяются.

ВАЖНО про локальный запуск: ``.env`` в корне репозитория содержит
``DATABASE_URL``/``REDIS_URL`` с docker-hostname'ами (``postgres``,
``redis``) — они резолвятся только *внутри* docker-compose сети. Если
Postgres/Redis подняты локально через
``docker compose up -d postgres redis`` (порты 5432/6379 проброшены на
localhost), при запуске pytest ВНЕ контейнера нужно переопределить эти
переменные окружения на localhost, например:

    DATABASE_URL=postgresql+asyncpg://postgres:changeme@localhost:5432/production_control \\
    REDIS_URL=redis://localhost:6379/0 \\
    poetry run pytest tests/integration/ -v

В CI (``.github/workflows/tests.yml``) переменные окружения уже указывают
на localhost (service containers GitHub Actions пробрасываются на
localhost), поэтому там никаких дополнительных телодвижений не требуется.
"""

from collections.abc import AsyncIterator
from itertools import count
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.cache import close_redis
from core.config import get_settings
from core.dependencies import get_db
from main import app

REPO_ROOT = Path(__file__).resolve().parents[2]

# Счётчик для генерации заведомо уникальных batch_number в рамках сессии —
# позволяет тестам не думать о коллизиях UNIQUE(batch_number, batch_date)
# друг с другом (хотя они и так изолированы откатом транзакции).
_batch_number_seq = count(900_000)


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    """Гарантирует, что схема БД актуальна (``alembic upgrade head``) перед
    запуском интеграционных тестов.

    Выполняется один раз за сессию через программный API Alembic (тот же
    ``alembic/env.py``, что использует ``DATABASE_URL`` из текущих
    настроек — см. docstring модуля). Если схема уже актуальна, Alembic
    просто ничего не делает (не ошибка). Если апгрейд не удаётся (например,
    БД временно недоступна на момент коллекции тестов), не валим сбор
    тестов здесь — сами тесты упадут с понятной ошибкой подключения, что
    даёт больше контекста, чем сбой в фикстуре collection-time.
    """
    from alembic import command
    from alembic.config import Config

    try:
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        command.upgrade(cfg, "head")
    except Exception as exc:  # noqa: BLE001 - best effort, см. docstring
        print(f"[integration conftest] alembic upgrade head пропущен/неудачен: {exc}")


@pytest_asyncio.fixture(scope="session")
async def _test_engine() -> AsyncIterator[Any]:
    """Отдельный движок для интеграционных тестов, с ``NullPool``.

    Намеренно НЕ переиспользует ``core.database.engine`` (пулируемый,
    ``pool_size``/``max_overflow`` из настроек): пул, рассчитанный на
    обслуживание параллельных HTTP-запросов приложения, не подходит для
    паттерна "одно соединение на тест с ручным SAVEPOINT/rollback" — при
    переиспользовании пулом одного и того же физического соединения между
    тестами наблюдался ``asyncpg.exceptions.InterfaceError: cannot perform
    operation: another operation is in progress`` начиная со второго теста.
    ``NullPool`` гарантирует новое физическое соединение на каждый
    ``connect()`` и его закрытие при возврате — полностью изолирует тесты
    друг от друга на уровне соединения.
    """
    settings = get_settings()
    test_engine = create_async_engine(str(settings.database_url), poolclass=NullPool)
    try:
        yield test_engine
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def db_session(_test_engine: Any) -> AsyncIterator[AsyncSession]:
    """Сессия, привязанная к транзакции на уровне соединения, откатываемой
    после теста (изоляция без модификации реальных данных)."""
    async with _test_engine.connect() as conn:
        outer_transaction = await conn.begin()
        session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()
            await outer_transaction.rollback()


@pytest_asyncio.fixture(autouse=True)
async def _reset_shared_redis_pool() -> AsyncIterator[None]:
    """Сбрасывает singleton-пул соединений Redis (``core.cache._pool``)
    вокруг каждого теста.

    ``pytest-asyncio`` (``asyncio_mode = "auto"``) по умолчанию создаёт
    отдельный event loop на каждую тестовую функцию. ``core.cache`` же
    держит ОДИН пул соединений на уровне модуля (создаётся лениво при
    первом ``get_redis()`` — используется и кешем в ``BatchService``/
    ``WebhookService``, и ``RateLimitMiddleware`` на каждый HTTP-запрос).
    Если этот пул создался в event loop'е одного теста, а переиспользуется
    в другом (со своим loop'ом), соединения из пула оказываются привязаны к
    уже закрытому loop'у -> ``RuntimeError: Event loop is closed`` при
    следующей операции. Закрываем пул до и после теста, чтобы он всегда
    пересоздавался заново в loop'е текущего теста.
    """
    await close_redis()
    try:
        yield
    finally:
        await close_redis()


@pytest_asyncio.fixture(autouse=True)
async def _flush_cache_db(_reset_shared_redis_pool: None) -> AsyncIterator[None]:
    """Очищает Redis DB кеша до и после каждого теста.

    Нужно по двум причинам:
    1. ``BatchService`` кеширует ``get_batch``/``get_batches_list`` в Redis
       вручную (не через транзакцию БД) — без очистки результаты одного
       теста (по данным, которые затем откатываются) могли бы "утечь" в
       следующий тест через кеш.
    2. ``RateLimitMiddleware`` тоже использует эту же Redis DB
       (``ratelimit:*`` ключи) — очистка не даёт тестам друг на друга
       влиять через счётчик лимита запросов.

    ``redis_data`` в docker-compose — персистентный volume, поэтому чистим
    и ДО теста тоже (не полагаемся только на очистку "после" предыдущего
    запуска). Зависит от ``_reset_shared_redis_pool``, чтобы этот ad-hoc
    клиент тоже создавался уже после сброса singleton-пула.
    """
    settings = get_settings()
    redis: Redis = Redis.from_url(  # type: ignore[type-arg]
        str(settings.redis_url),
        db=settings.redis_cache_db,
        decode_responses=True,
    )
    try:
        await redis.flushdb()
        yield
    finally:
        await redis.flushdb()
        await redis.aclose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Асинхронный HTTP-клиент поверх реального FastAPI-приложения
    (in-process, без поднятия сервера), с ``get_db`` переопределённым на
    транзакционную сессию ``db_session``."""

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        # Реплицирует поведение настоящего ``core.dependencies.get_db`` —
        # commit при успехе, rollback (на SAVEPOINT, не на внешнюю
        # транзакцию — см. join_transaction_mode="create_savepoint" в
        # db_session) при исключении. Важно делать именно так, а не просто
        # ``yield db_session``: когда путь-обработчик поднимает AppError
        # (404/409/...), перехватываемый зарегистрированным
        # ``@app.exception_handler(AppError)``, код ПОСЛЕ ``yield`` в
        # зависимости с yield может быть не выполнен (известная особенность
        # FastAPI при наличии кастомных обработчиков исключений) — без
        # явного rollback здесь сессия/SAVEPOINT остаются в открытом
        # состоянии и следующий запрос в том же тесте падает с asyncpg
        # ошибкой "another operation is in progress".
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)


# --- Фабрики валидных payload'ов ---------------------------------------


def make_batch_create_payload(**overrides: Any) -> dict[str, Any]:
    """Валидный элемент запроса создания партии (кириллические поля 1С,
    см. ``api/v1/schemas/batch.py::BatchCreateItem``).

    ``НомерПартии`` по умолчанию берётся из общего счётчика (заведомо
    уникален в рамках сессии) — передайте свой через ``overrides``, если
    тесту нужно конкретное значение (например, чтобы намеренно столкнуть
    дубликаты)."""
    payload: dict[str, Any] = {
        "СтатусЗакрытия": False,
        "ПредставлениеЗаданияНаСмену": "Изготовить 1000 болтов М10",
        "РабочийЦентр": "Цех №1",
        "Смена": "1 смена",
        "Бригада": "Бригада Иванова",
        "НомерПартии": next(_batch_number_seq),
        "ДатаПартии": "2024-01-30",
        "Номенклатура": "Болт М10х50",
        "КодЕКН": "EKN-12345",
        "ИдентификаторРЦ": "RC-001",
        "ДатаВремяНачалаСмены": "2024-01-30T08:00:00",
        "ДатаВремяОкончанияСмены": "2024-01-30T20:00:00",
    }
    payload.update(overrides)
    return payload


def make_webhook_create_payload(**overrides: Any) -> dict[str, Any]:
    """Валидный payload создания подписки на вебхук
    (см. ``api/v1/schemas/webhook.py::WebhookCreateRequest``)."""
    payload: dict[str, Any] = {
        "url": "https://example.com/hooks/production-control",
        "events": ["batch_created", "batch_closed"],
        "secret_key": "test-secret-key-0123456789",
        "retry_count": 3,
        "timeout": 10,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def batch_payload_factory() -> Any:
    return make_batch_create_payload


@pytest.fixture
def webhook_payload_factory() -> Any:
    return make_webhook_create_payload
