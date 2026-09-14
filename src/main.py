from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from api.v1.routers.batches import router as batches_router
from api.v1.routers.products import router as products_router
from api.v1.routers.work_centers import router as work_centers_router
from core.cache import check_redis_connection, close_redis
from core.config import get_settings
from core.database import check_db_connection
from core.exceptions import register_exception_handlers
from storage.minio_service import MinIOService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Управляет ресурсами при запуске и остановке приложения."""
    logger.info("Запуск '{}' v{}", app.title, app.version)
    MinIOService().ensure_buckets()
    logger.info("Бакеты MinIO проверены/созданы")
    yield
    logger.info("Завершение работы — закрываем пул Redis")
    await close_redis()


def create_app() -> FastAPI:
    """Создаёт и настраивает экземпляр FastAPI."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.include_router(batches_router, prefix="/api/v1")
    app.include_router(products_router, prefix="/api/v1")
    app.include_router(work_centers_router, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        """Возвращает состояние сервиса и его зависимостей (liveness/readiness)."""
        db_ok = await check_db_connection()
        redis_ok = await check_redis_connection()

        healthy = db_ok and redis_ok
        return {
            "status": "ok" if healthy else "degraded",
            "database": "ok" if db_ok else "unavailable",
            "redis": "ok" if redis_ok else "unavailable",
            "version": settings.app_version,
        }

    return app


app = create_app()
