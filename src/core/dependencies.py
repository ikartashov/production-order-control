from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import get_redis
from core.database import AsyncSessionFactory
from storage.minio_service import MinIOService


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Возвращает ``AsyncSession`` на время обработки одного HTTP-запроса.

    Выполняет commit при успехе; откат и повторный raise при необработанном исключении.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_redis_client() -> Redis:  # type: ignore[type-arg]
    """Возвращает общий асинхронный Redis-клиент."""
    client: Redis = await get_redis()  # type: ignore[type-arg]
    return client


def get_minio_service() -> MinIOService:
    """Возвращает сервис для работы с файловым хранилищем MinIO."""
    return MinIOService()


# Сокращённые аннотации

DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis_client)]
MinioClient = Annotated[MinIOService, Depends(get_minio_service)]
