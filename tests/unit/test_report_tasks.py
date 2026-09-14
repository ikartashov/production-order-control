from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from data.models.batch import Batch
from data.models.product import Product
from tasks.report_tasks import (
    _compute_stats,
    _generate_batch_report_async,
    generate_batch_report,
)


@asynccontextmanager
async def _fake_session() -> Any:
    yield AsyncMock()


class TestComputeStats:
    """Тесты для расчёта статистики по продукции партии."""

    def test_computes_percentages(
        self, product: Product, aggregated_product: Product
    ) -> None:
        batch = MagicMock()
        batch.products = [product, aggregated_product]

        stats = _compute_stats(batch)

        assert stats == {
            "total": 2,
            "aggregated": 1,
            "remaining": 1,
            "percent": 50.0,
        }

    def test_handles_empty_products(self) -> None:
        batch = MagicMock()
        batch.products = []

        stats = _compute_stats(batch)

        assert stats == {
            "total": 0,
            "aggregated": 0,
            "remaining": 0,
            "percent": 0.0,
        }


class TestGenerateBatchReportAsync:
    """Смоук-тесты формирования и загрузки отчёта (без проверки внутренностей файла)."""

    async def test_generates_excel_and_uploads(
        self, batch: Batch, product: Product, aggregated_product: Product
    ) -> None:
        batch.products = [product, aggregated_product]
        mock_batch_service = AsyncMock()
        mock_batch_service.get_batch.return_value = batch

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/report.xlsx"

        with (
            patch("tasks.report_tasks.get_session", _fake_session),
            patch("tasks.report_tasks.BatchService", return_value=mock_batch_service),
            patch("tasks.report_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _generate_batch_report_async(batch.id, "excel")

        assert result["success"] is True
        assert result["file_url"] == "https://minio.example/report.xlsx"
        assert result["file_name"] == f"batch_{batch.id}_report.xlsx"
        assert result["file_size"] > 0
        assert "expires_at" in result

        mock_minio.upload_file.assert_called_once()
        call_kwargs = mock_minio.upload_file.call_args.kwargs
        assert call_kwargs["bucket"] == "reports"
        assert call_kwargs["object_name"] == f"batch_{batch.id}_report.xlsx"

    async def test_generates_pdf_and_uploads(
        self, batch: Batch, product: Product
    ) -> None:
        batch.products = [product]
        mock_batch_service = AsyncMock()
        mock_batch_service.get_batch.return_value = batch

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/report.pdf"

        with (
            patch("tasks.report_tasks.get_session", _fake_session),
            patch("tasks.report_tasks.BatchService", return_value=mock_batch_service),
            patch("tasks.report_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _generate_batch_report_async(batch.id, "pdf")

        assert result["file_name"] == f"batch_{batch.id}_report.pdf"
        assert result["file_size"] > 0

        call_kwargs = mock_minio.upload_file.call_args.kwargs
        assert call_kwargs["object_name"] == f"batch_{batch.id}_report.pdf"

    async def test_defaults_to_excel_for_unknown_format(
        self, batch: Batch, product: Product
    ) -> None:
        batch.products = [product]
        mock_batch_service = AsyncMock()
        mock_batch_service.get_batch.return_value = batch

        mock_minio = MagicMock()
        mock_minio.upload_file.return_value = "https://minio.example/report.xlsx"

        with (
            patch("tasks.report_tasks.get_session", _fake_session),
            patch("tasks.report_tasks.BatchService", return_value=mock_batch_service),
            patch("tasks.report_tasks.MinIOService", return_value=mock_minio),
        ):
            result = await _generate_batch_report_async(batch.id, "unknown")

        assert result["file_name"] == f"batch_{batch.id}_report.xlsx"


class TestGenerateBatchReportTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_async_runner(self) -> None:
        with patch(
            "tasks.report_tasks._generate_batch_report_async",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "file_url": "url",
                    "file_name": "name",
                    "file_size": 1,
                    "expires_at": "2024-01-01T00:00:00+00:00",
                }
            ),
        ) as mock_async:
            result = generate_batch_report.run(1, "excel", None)

        assert result["success"] is True
        mock_async.assert_awaited_once_with(1, "excel")
