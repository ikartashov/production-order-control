from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import ConflictError
from tasks.import_tasks import (
    _import_batches_from_file_async,
    import_batches_from_file,
)

_HEADER = (
    "НомерПартии,ДатаПартии,Номенклатура,РабочийЦентр,ИдентификаторРЦ,Смена,Бригада,"
    "КодЕКН,ДатаВремяНачалаСмены,ДатаВремяОкончанияСмены,"
    "ПредставлениеЗаданияНаСмену,СтатусЗакрытия\n"
)


def _row(batch_number: int) -> str:
    return (
        f"{batch_number},2024-01-30,Болт М10,Цех №1,RC-001,1 смена,Иванов,"
        f"EKN-{batch_number},2024-01-30 08:00:00,2024-01-30 20:00:00,"
        f"Задание {batch_number},False\n"
    )


@asynccontextmanager
async def _fake_session() -> Any:
    yield AsyncMock()


def _write_csv(bucket: str, object_name: str, file_path: str, content: str) -> None:
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


class TestImportBatchesFromFileAsync:
    """Тесты построчной обработки импорта с частичным успехом."""

    async def test_all_rows_created(self) -> None:
        content = _HEADER + _row(1001) + _row(1002)
        mock_minio = MagicMock()
        mock_minio.download_file.side_effect = lambda b, o, p: _write_csv(
            b, o, p, content
        )

        mock_batch_service = AsyncMock()
        mock_batch_service.create_batches.return_value = [MagicMock()]

        with (
            patch("tasks.import_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.import_tasks.get_session", _fake_session),
            patch("tasks.import_tasks.BatchService", return_value=mock_batch_service),
        ):
            result = await _import_batches_from_file_async("import_test.csv")

        assert result["total_rows"] == 2
        assert result["created"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == []
        assert mock_batch_service.create_batches.await_count == 2

    async def test_one_bad_row_does_not_stop_import(self) -> None:
        content = _HEADER + _row(1001) + _row(1002)
        mock_minio = MagicMock()
        mock_minio.download_file.side_effect = lambda b, o, p: _write_csv(
            b, o, p, content
        )

        mock_batch_service = AsyncMock()
        mock_batch_service.create_batches.side_effect = [
            [MagicMock()],
            ConflictError("Партия с номером 1002 уже существует"),
        ]

        with (
            patch("tasks.import_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.import_tasks.get_session", _fake_session),
            patch("tasks.import_tasks.BatchService", return_value=mock_batch_service),
        ):
            result = await _import_batches_from_file_async("import_test.csv")

        assert result["total_rows"] == 2
        assert result["created"] == 1
        assert result["skipped"] == 1
        assert result["errors"] == [
            {"row": 2, "error": "Партия с номером 1002 уже существует"}
        ]

    async def test_row_missing_column_recorded_as_error(self) -> None:
        bad_header = _HEADER.replace("НомерПартии,", "")
        bad_row = _row(1001).split(",", 1)[1]
        content = bad_header + bad_row

        mock_minio = MagicMock()
        mock_minio.download_file.side_effect = lambda b, o, p: _write_csv(
            b, o, p, content
        )

        mock_batch_service = AsyncMock()

        with (
            patch("tasks.import_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.import_tasks.get_session", _fake_session),
            patch("tasks.import_tasks.BatchService", return_value=mock_batch_service),
        ):
            result = await _import_batches_from_file_async("import_test.csv")

        assert result["total_rows"] == 1
        assert result["created"] == 0
        assert result["skipped"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["row"] == 1
        mock_batch_service.create_batches.assert_not_awaited()

    async def test_empty_file_reports_zero_rows(self) -> None:
        content = _HEADER
        mock_minio = MagicMock()
        mock_minio.download_file.side_effect = lambda b, o, p: _write_csv(
            b, o, p, content
        )

        mock_batch_service = AsyncMock()

        with (
            patch("tasks.import_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.import_tasks.get_session", _fake_session),
            patch("tasks.import_tasks.BatchService", return_value=mock_batch_service),
        ):
            result = await _import_batches_from_file_async("import_test.csv")

        assert result["total_rows"] == 0
        assert result["created"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == []


class TestImportBatchesFromFileTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_async_runner(self) -> None:
        with patch(
            "tasks.import_tasks._import_batches_from_file_async",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "total_rows": 0,
                    "created": 0,
                    "skipped": 0,
                    "errors": [],
                }
            ),
        ) as mock_async:
            result = import_batches_from_file.run("import_test.csv")

        assert result["success"] is True
        mock_async.assert_awaited_once_with("import_test.csv")
