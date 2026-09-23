from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from data.models.batch import Batch
from tasks.export_tasks import (
    _export_batches_to_file_async,
    _fetch_all,
    export_batches_to_file,
)


@asynccontextmanager
async def _fake_session() -> Any:
    yield AsyncMock()


class TestFetchAll:
    """Тесты постраничной выгрузки партий из репозитория."""

    async def test_returns_all_batches_from_single_page(self, batch: Batch) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_list.return_value = ([batch, batch], 2)

        result = await _fetch_all(mock_repo, {"is_closed": False})

        assert result == [batch, batch]
        mock_repo.get_list.assert_awaited_once_with(
            offset=0, limit=500, is_closed=False
        )

    async def test_stops_when_total_reached_across_pages(self, batch: Batch) -> None:
        mock_repo = AsyncMock()
        # Первая "страница" ровно заполняет page_size с total, превышающим её —
        # цикл должен остановиться, когда offset достигнет total.
        page = [batch] * 3
        mock_repo.get_list.return_value = (page, 3)

        result = await _fetch_all(mock_repo, {})

        assert len(result) == 3
        mock_repo.get_list.assert_awaited_once()


class TestExportBatchesToFileAsync:
    """Тесты оркестрации экспорта: фильтры, сборка файла, загрузка в MinIO."""

    async def test_drops_unsupported_filter_keys(self, batch: Batch) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_list.return_value = ([batch], 1)

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/export.xlsx"

        filters = {
            "is_closed": False,
            "date_from": "2024-01-01",
            "date_to": "2024-02-01",
        }

        with (
            patch("tasks.export_tasks.get_session", _fake_session),
            patch("tasks.export_tasks.BatchRepository", return_value=mock_repo),
            patch("tasks.export_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _export_batches_to_file_async(filters, "excel")

        called_kwargs = mock_repo.get_list.call_args.kwargs
        assert "date_from" not in called_kwargs
        assert "date_to" not in called_kwargs
        assert called_kwargs["is_closed"] is False
        assert result["total_batches"] == 1

    async def test_uploads_excel_to_exports_bucket(self, batch: Batch) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_list.return_value = ([batch], 1)

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/export.xlsx"

        with (
            patch("tasks.export_tasks.get_session", _fake_session),
            patch("tasks.export_tasks.BatchRepository", return_value=mock_repo),
            patch("tasks.export_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _export_batches_to_file_async({}, "excel")

        assert result == {
            "success": True,
            "file_url": "https://minio.example/export.xlsx",
            "total_batches": 1,
        }
        mock_minio.upload_file.assert_called_once()
        call_kwargs = mock_minio.upload_file.call_args.kwargs
        assert call_kwargs["bucket"] == "exports"
        assert call_kwargs["object_name"].endswith(".xlsx")

    async def test_uploads_csv_to_exports_bucket(self, batch: Batch) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_list.return_value = ([batch], 1)

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/export.csv"

        with (
            patch("tasks.export_tasks.get_session", _fake_session),
            patch("tasks.export_tasks.BatchRepository", return_value=mock_repo),
            patch("tasks.export_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _export_batches_to_file_async({}, "csv")

        assert result["total_batches"] == 1
        call_kwargs = mock_minio.upload_file.call_args.kwargs
        assert call_kwargs["object_name"].endswith(".csv")

    async def test_no_batches_still_uploads_empty_file(self) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_list.return_value = ([], 0)

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/export.xlsx"

        with (
            patch("tasks.export_tasks.get_session", _fake_session),
            patch("tasks.export_tasks.BatchRepository", return_value=mock_repo),
            patch("tasks.export_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _export_batches_to_file_async({}, "excel")

        assert result["total_batches"] == 0
        mock_minio.upload_file.assert_called_once()


class TestExportBatchesToFileTask:
    """Тест простой (небиндовой) Celery-задачи."""

    def test_run_invokes_async_runner(self) -> None:
        with patch(
            "tasks.export_tasks._export_batches_to_file_async",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "file_url": "url",
                    "total_batches": 0,
                }
            ),
        ) as mock_async:
            result = export_batches_to_file.run({"is_closed": True}, "csv")

        assert result["success"] is True
        mock_async.assert_awaited_once_with({"is_closed": True}, "csv")
