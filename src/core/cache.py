import json
from typing import Any

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


async def cache_delete_pattern(pattern: str) -> None:
    """Удаляет все ключи, соответствующие шаблону *pattern* (например, ``"batches_list:*"``)."""
    redis = await get_redis()
    keys: list[str] = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)
        logger.debug("Инвалидировано {} ключей по шаблону '{}'", len(keys), pattern)
