import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from loguru import logger
from redis.asyncio import ConnectionPool, Redis
from redis.exceptions import RedisError

from core.config import get_settings

# Пул соединений

_pool: ConnectionPool | None = None  # type: ignore[type-arg]


def _get_pool() -> ConnectionPool:  # type: ignore[type-arg]
    """Возвращает пул соединений уровня модуля, создавая его при первом вызове."""
    global _pool  # noqa: PLW0603
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool.from_url(
            str(settings.redis_url),
            db=settings.redis_cache_db,
            decode_responses=True,
            max_connections=20,
        )
        logger.info("Пул соединений Redis создан: {}", settings.redis_url)
    return _pool


async def get_redis() -> Redis:  # type: ignore[type-arg]
    """Возвращает асинхронный Redis-клиент на основе общего пула.

    Вызывающей стороне **не нужно** закрывать соединение —
    оно автоматически возвращается в пул.
    """
    return Redis(connection_pool=_get_pool())


async def close_redis() -> None:
    """Освобождает и закрывает пул соединений Redis.

    Вызывается при завершении работы приложения (событие lifespan).
    """
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
        logger.info("Пул соединений Redis закрыт")


async def check_redis_connection() -> bool:
    """Проверяет доступность Redis. Используется эндпоинтом /health."""
    try:
        redis = await get_redis()
        await redis.ping()
        return True
    except RedisError as exc:
        logger.error("Проверка подключения к Redis завершилась ошибкой: {}", exc)
        return False


# Вспомогательные функции


async def cache_set(key: str, value: Any, ttl: int) -> None:
    """Сериализует *value* в JSON и сохраняет под ключом *key* с TTL *ttl* секунд."""
    redis = await get_redis()
    await redis.set(key, json.dumps(value, default=str), ex=ttl)


async def cache_get(key: str) -> Any | None:
    """Возвращает десериализованное значение по ключу *key* или ``None``, если ключ не найден."""
    redis = await get_redis()
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def cache_delete(key: str) -> None:
    """Удаляет один ключ кеша."""
    redis = await get_redis()
    await redis.delete(key)


_SCAN_DELETE_CHUNK_SIZE = 500


async def cache_delete_pattern(pattern: str) -> None:
    """Удаляет все ключи, соответствующие шаблону *pattern* (например, ``"batches_list:*"``).

    Использует неблокирующий ``SCAN`` (через ``scan_iter``) вместо ``KEYS``,
    чтобы не блокировать однопоточный event loop Redis O(N)-сканированием
    всего keyspace на время выполнения. Найденные ключи удаляются пачками
    по ``_SCAN_DELETE_CHUNK_SIZE`` штук, чтобы не отправлять один огромный
    ``DEL`` с тысячами аргументов.
    """
    redis = await get_redis()
    deleted_count = 0
    chunk: list[str] = []
    async for key in redis.scan_iter(match=pattern):
        chunk.append(key)
        if len(chunk) >= _SCAN_DELETE_CHUNK_SIZE:
            await redis.delete(*chunk)
            deleted_count += len(chunk)
            chunk = []
    if chunk:
        await redis.delete(*chunk)
        deleted_count += len(chunk)
    if deleted_count:
        logger.debug("Инвалидировано {} ключей по шаблону '{}'", deleted_count, pattern)


# Декоратор кеширования


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def cached(ttl: int, key_prefix: str) -> Callable[[F], F]:
    """Декоратор-фабрика: кеширует результат асинхронной функции/метода в Redis.

    Ключ кеша строится из ``key_prefix`` + позиционных аргументов (аргумент
    под индексом 0 исключается, т.к. декоратор рассчитан на методы
    экземпляра и ``self`` не JSON-сериализуем и не должен влиять на ключ) +
    именованных аргументов, сериализованных детерминированно через
    ``json.dumps(kwargs, sort_keys=True, default=str)``.

    При кеш-хите десериализованное значение возвращается без вызова
    обёрнутой функции; при промахе функция вызывается, результат кешируется
    через ``cache_set`` (который сериализует его как есть через
    ``json.dumps(value, default=str)``) и возвращается.

    Подходит только для функций, чей результат уже является простым
    JSON-сериализуемым значением (dict/list/str/int/...). Для методов,
    возвращающих SQLAlchemy ORM-объекты (например,
    ``BatchService.get_batch``/``get_batches_list``), этот декоратор
    напрямую не используется — ``cache_set``/``cache_get`` не умеют
    корректно провести ORM-объект через ``json.dumps``/``json.loads`` и
    сохранить его связи (``work_center``, ``products``). Там кеширование
    сделано вручную вокруг маленького сериализатора ORM<->dict — см.
    комментарии в ``domain/services/batch_service.py``.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            key_args = args[1:]
            parts = [key_prefix, *(str(a) for a in key_args)]
            if kwargs:
                parts.append(json.dumps(kwargs, sort_keys=True, default=str))
            cache_key = ":".join(parts)

            cached_value = await cache_get(cache_key)
            if cached_value is not None:
                return cached_value

            result = await func(*args, **kwargs)
            await cache_set(cache_key, result, ttl)
            return result

        return cast(F, wrapper)

    return decorator
