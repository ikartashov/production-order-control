"""Celery-задача импорта партий из Excel/CSV файла (построчно, с частичным успехом)."""

import asyncio
import os
import tempfile
from typing import Any

import pandas as pd
from loguru import logger

from celery_app import celery_app
from core.database import get_session
from core.exceptions import ConflictError, ValidationError
from domain.services.batch_service import BatchService
from storage.minio_service import MinIOService

_IMPORTS_BUCKET = "imports"

# Соответствие кириллических колонок файла внутренним ключам dict,
# зеркалирует маппинг в api/v1/routers/batches.py::create_batches.
_COLUMN_MAP: dict[str, str] = {
    "СтатусЗакрытия": "is_closed",
    "ПредставлениеЗаданияНаСмену": "task_description",
    "РабочийЦентр": "wc_name",
    "ИдентификаторРЦ": "wc_identifier",
    "Смена": "shift",
    "Бригада": "team",
    "НомерПартии": "batch_number",
    "ДатаПартии": "batch_date",
    "Номенклатура": "nomenclature",
    "КодЕКН": "ekn_code",
    "ДатаВремяНачалаСмены": "shift_start",
    "ДатаВремяОкончанияСмены": "shift_end",
}


@celery_app.task(bind=True, max_retries=1)  # type: ignore[untyped-decorator]
def import_batches_from_file(
    self: Any,
    file_url: str,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Импортировать партии из файла (Excel/CSV), загруженного в MinIO.

    ``file_url`` — имя объекта (object_name) в бакете ``imports``.
    """
    return asyncio.run(_import_batches_from_file_async(file_url))


async def _import_batches_from_file_async(object_name: str) -> dict[str, Any]:
    _, ext = os.path.splitext(object_name)
    ext = ext.lower()

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        temp_path = tmp.name

    try:
        MinIOService().download_file(_IMPORTS_BUCKET, object_name, temp_path)

        df = pd.read_csv(temp_path) if ext == ".csv" else pd.read_excel(temp_path)

        total_rows = len(df)
        created = 0
        errors: list[dict[str, Any]] = []

        async with get_session() as session:
            service = BatchService(session)

            for row_num, (_, row) in enumerate(df.iterrows(), start=1):
                try:
                    item = _row_to_item(row)
                    await service.create_batches([item])
                    created += 1
                except (ConflictError, ValidationError) as exc:
                    errors.append({"row": row_num, "error": exc.detail})
                except (KeyError, ValueError, TypeError) as exc:
                    errors.append({"row": row_num, "error": str(exc)})
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # TODO(webhooks): fire "import_completed" event via WebhookService once wired

    logger.info(
        "Импорт партий завершён: всего={} создано={} ошибок={}",
        total_rows,
        created,
        len(errors),
    )

    return {
        "success": True,
        "total_rows": total_rows,
        "created": created,
        "skipped": len(errors),
        "errors": errors,
    }


def _row_to_item(row: "pd.Series[Any]") -> dict[str, Any]:
    """Преобразует строку DataFrame в dict для ``BatchService.create_batches``."""
    raw: dict[str, Any] = {}
    for column, key in _COLUMN_MAP.items():
        raw[key] = row[column]

    is_closed = raw["is_closed"]
    if pd.isna(is_closed):
        is_closed = False

    return {
        "is_closed": bool(is_closed),
        "task_description": str(raw["task_description"]),
        "wc_name": str(raw["wc_name"]),
        "wc_identifier": str(raw["wc_identifier"]),
        "shift": str(raw["shift"]),
        "team": str(raw["team"]),
        "batch_number": int(raw["batch_number"]),
        "batch_date": pd.to_datetime(raw["batch_date"]).date(),
        "nomenclature": str(raw["nomenclature"]),
        "ekn_code": str(raw["ekn_code"]),
        "shift_start": pd.to_datetime(raw["shift_start"]).to_pydatetime(),
        "shift_end": pd.to_datetime(raw["shift_end"]).to_pydatetime(),
    }
