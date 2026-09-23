import os
from datetime import timedelta

from loguru import logger
from minio import Minio
from minio.datatypes import Object

from storage.minio_client import get_minio_client

# Бакеты, инициализируемые при старте приложения

BUCKETS: list[str] = ["reports", "exports", "imports"]

# Соответствие расширений файлов MIME-типам

_CONTENT_TYPES: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".json": "application/json",
}

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _guess_content_type(file_path: str) -> str:
    """Определяет MIME-тип файла по его расширению."""
    _, ext = os.path.splitext(file_path)
    return _CONTENT_TYPES.get(ext.lower(), _DEFAULT_CONTENT_TYPE)


class MinIOService:
    """Инкапсулирует операции с файловым хранилищем MinIO (S3-совместимым)."""

    def __init__(self, client: Minio | None = None) -> None:
        self._client = client if client is not None else get_minio_client()

    def ensure_buckets(self) -> None:
        """Создаёт бакеты из ``BUCKETS``, если они ещё не существуют."""
        for bucket in BUCKETS:
            if not self._client.bucket_exists(bucket):
                self._client.make_bucket(bucket)
                logger.info("Бакет MinIO создан: {}", bucket)
            else:
                logger.debug("Бакет MinIO уже существует: {}", bucket)

    def upload_file(
        self,
        bucket: str,
        file_path: str,
        object_name: str | None = None,
        expires_days: int = 7,
    ) -> str:
        """Загружает файл в бакет и возвращает временную (presigned) ссылку на скачивание."""
        object_name = object_name or os.path.basename(file_path)
        content_type = _guess_content_type(file_path)
        self._client.fput_object(
            bucket,
            object_name,
            file_path,
            content_type=content_type,
        )
        logger.info("Файл загружен в MinIO: {}/{}", bucket, object_name)
        return self.get_presigned_url(bucket, object_name, expires_days=expires_days)

    def download_file(self, bucket: str, object_name: str, file_path: str) -> None:
        """Скачивает объект из бакета в локальный файл."""
        self._client.fget_object(bucket, object_name, file_path)

    def delete_file(self, bucket: str, object_name: str) -> None:
        """Удаляет объект из бакета."""
        self._client.remove_object(bucket, object_name)
        logger.info("Файл удалён из MinIO: {}/{}", bucket, object_name)

    def list_files(self, bucket: str, prefix: str | None = None) -> list[Object]:
        """Возвращает список объектов в бакете, опционально отфильтрованных по префиксу."""
        return list(self._client.list_objects(bucket, prefix=prefix, recursive=True))

    def get_presigned_url(
        self, bucket: str, object_name: str, expires_days: int = 7
    ) -> str:
        """Возвращает временную (presigned) ссылку на скачивание объекта."""
        url: str = self._client.presigned_get_object(
            bucket, object_name, expires=timedelta(days=expires_days)
        )
        return url
