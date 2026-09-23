"""Celery-задача экспорта партий в Excel/CSV файл."""

import asyncio
import csv
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from openpyxl import Workbook

from celery_app import celery_app
from core.database import get_session
from data.models.batch import Batch
from data.repositories.batch_repository import BatchRepository
from storage.minio_service import MinIOService

_EXPORTS_BUCKET = "exports"

# Ключи фильтров, которые действительно понимает BatchRepository.get_list.
# Прочие ключи (например date_from/date_to — диапазонная фильтрация) не
# поддерживаются репозиторием и молча отбрасываются.
_ALLOWED_FILTER_KEYS = {
    "is_closed",
    "batch_number",
    "batch_date",
    "work_center_id",
    "shift",
}

_HEADER = [
    "ID",
    "Номер партии",
    "Дата партии",
    "Номенклатура",
    "Рабочий центр",
    "Смена",
    "Бригада",
    "Закрыта",
    "Дата закрытия",
]


@celery_app.task  # type: ignore[untyped-decorator]
def export_batches_to_file(
    filters: dict[str, Any], format: str = "excel"
) -> dict[str, Any]:
    """Экспортировать партии, отфильтрованные по ``filters``, в Excel/CSV файл в MinIO."""
    return asyncio.run(_export_batches_to_file_async(filters, format))


async def _export_batches_to_file_async(
    filters: dict[str, Any], format: str
) -> dict[str, Any]:
    allowed_filters = {
        key: value
        for key, value in filters.items()
        if key in _ALLOWED_FILTER_KEYS and value is not None
    }

    async with get_session() as session:
        repo = BatchRepository(Batch, session)
        batches = await _fetch_all(repo, allowed_filters)

        ext = "csv" if format == "csv" else "xlsx"
        object_name = f"batches_export_{datetime.now(UTC):%Y%m%dT%H%M%S}.{ext}"

        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            temp_path = tmp.name

        try:
            if format == "csv":
                _build_csv(batches, temp_path)
            else:
                _build_excel(batches, temp_path)

            file_url = MinIOService().upload_file(
                bucket=_EXPORTS_BUCKET,
                file_path=temp_path,
                object_name=object_name,
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    logger.info(
        "Экспорт партий завершён: {} шт., формат={}, файл={}",
        len(batches),
        format,
        object_name,
    )

    return {
        "success": True,
        "file_url": file_url,
        "total_batches": len(batches),
    }


async def _fetch_all(repo: BatchRepository, filters: dict[str, Any]) -> list[Batch]:
    """Постранично выгружает все партии, подходящие под фильтры."""
    batches: list[Batch] = []
    offset = 0
    page_size = 500

    while True:
        page, total = await repo.get_list(offset=offset, limit=page_size, **filters)
        batches.extend(page)
        offset += page_size
        if len(page) < page_size or offset >= total:
            break

    return batches


def _batch_row(batch: Batch) -> list[Any]:
    return [
        batch.id,
        batch.batch_number,
        batch.batch_date.isoformat(),
        batch.nomenclature,
        batch.work_center.name,
        batch.shift,
        batch.team,
        "Да" if batch.is_closed else "Нет",
        batch.closed_at.isoformat() if batch.closed_at else "",
    ]


def _build_excel(batches: list[Batch], file_path: str) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Партии"
    ws.append(_HEADER)
    for batch in batches:
        ws.append(_batch_row(batch))
    wb.save(file_path)


def _build_csv(batches: list[Batch], file_path: str) -> None:
    with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(_HEADER)
        for batch in batches:
            writer.writerow(_batch_row(batch))
