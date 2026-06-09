from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.core.config import get_settings


class Base(DeclarativeBase):
    """Общая декларативная база для всех ORM-моделей."""


def _build_engine() -> AsyncEngine:
    """Создаёт асинхронный движок на основе настроек приложения."""
    settings = get_settings()
    engine = create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        echo=settings.debug,
    )
    logger.info("Движок базы данных создан: {}", settings.database_url)
    return engine


engine = _build_engine()

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Асинхронный контекстный менеджер, возвращающий сессию базы данных.

    Выполняет commit при успехе, откат и повторный raise при любом исключении.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_connection() -> bool:
    """Проверяет доступность базы данных. Используется эндпоинтом /health."""
    try:
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Проверка подключения к БД завершилась ошибкой: {}", exc)
        return False
