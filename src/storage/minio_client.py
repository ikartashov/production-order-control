from loguru import logger
from minio import Minio

from core.config import get_settings

# Клиент MinIO уровня модуля

_client: Minio | None = None


def get_minio_client() -> Minio:
    """Возвращает клиент MinIO уровня модуля, создавая его при первом вызове."""
    global _client  # noqa: PLW0603
    if _client is None:
        settings = get_settings()
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        logger.info("Клиент MinIO создан: {}", settings.minio_endpoint)
    return _client
