from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import ConflictError, NotFoundError, ValidationError
from tasks.aggregation_tasks import (
    _aggregate_products_batch_async,
    aggregate_products_batch,
)


@asynccontextmanager
async def _fake_session() -> Any:
    yield AsyncMock()


class TestAggregateProductsBatchAsync:
    """Тесты для внутренней асинхронной логики частично-успешной агрегации."""

    async def test_all_codes_succeed(self) -> None:
        """Все коды успешно агрегированы поштучно."""
        task = MagicMock()
        mock_service = AsyncMock()
        mock_service.aggregate_products.return_value = []

        with (
            patch("tasks.aggregation_tasks.get_session", _fake_session),
            patch("tasks.aggregation_tasks.ProductService", return_value=mock_service),
        ):
            result = await _aggregate_products_batch_async(task, 1, ["A", "B", "C"])

        assert result == {
            "success": True,
            "total": 3,
            "aggregated": 3,
            "failed": 0,
            "errors": [],
        }
        assert mock_service.aggregate_products.await_count == 3
        assert task.update_state.call_count == 3

    async def test_partial_failure_accumulates_errors(self) -> None:
        """Ошибки по отдельным кодам не прерывают обработку остальных."""
        task = MagicMock()
        mock_service = AsyncMock()

        async def side_effect(batch_id: int, codes: list[str]) -> list[Any]:
            if codes[0] == "BAD":
                raise NotFoundError("Код не найден")
            return []

        mock_service.aggregate_products.side_effect = side_effect

        with (
            patch("tasks.aggregation_tasks.get_session", _fake_session),
            patch("tasks.aggregation_tasks.ProductService", return_value=mock_service),
        ):
            result = await _aggregate_products_batch_async(
                task, 1, ["OK1", "BAD", "OK2"]
            )

        assert result["total"] == 3
        assert result["aggregated"] == 2
        assert result["failed"] == 1
        assert result["errors"] == [{"code": "BAD", "reason": "Код не найден"}]

    async def test_catches_conflict_and_validation_errors(self) -> None:
        """ConflictError и ValidationError тоже накапливаются как частичные ошибки."""
        task = MagicMock()
        mock_service = AsyncMock()
        mock_service.aggregate_products.side_effect = [
            ConflictError("Уже агрегирован"),
            ValidationError("Партия закрыта"),
        ]

        with (
            patch("tasks.aggregation_tasks.get_session", _fake_session),
            patch("tasks.aggregation_tasks.ProductService", return_value=mock_service),
        ):
            result = await _aggregate_products_batch_async(task, 1, ["C1", "C2"])

        assert result["aggregated"] == 0
        assert result["failed"] == 2
        assert result["errors"] == [
            {"code": "C1", "reason": "Уже агрегирован"},
            {"code": "C2", "reason": "Партия закрыта"},
        ]

    async def test_empty_codes_list(self) -> None:
        """Пустой список кодов не вызывает ошибок и возвращает нулевую статистику."""
        task = MagicMock()
        mock_service = AsyncMock()

        with (
            patch("tasks.aggregation_tasks.get_session", _fake_session),
            patch("tasks.aggregation_tasks.ProductService", return_value=mock_service),
        ):
            result = await _aggregate_products_batch_async(task, 1, [])

        assert result == {
            "success": True,
            "total": 0,
            "aggregated": 0,
            "failed": 0,
            "errors": [],
        }
        mock_service.aggregate_products.assert_not_awaited()

    async def test_progress_reported_with_correct_percentage(self) -> None:
        """update_state вызывается с корректным прогрессом на каждой итерации."""
        task = MagicMock()
        mock_service = AsyncMock()
        mock_service.aggregate_products.return_value = []

        with (
            patch("tasks.aggregation_tasks.get_session", _fake_session),
            patch("tasks.aggregation_tasks.ProductService", return_value=mock_service),
        ):
            await _aggregate_products_batch_async(task, 1, ["A", "B"])

        last_call = task.update_state.call_args_list[-1]
        assert last_call.kwargs["state"] == "PROGRESS"
        assert last_call.kwargs["meta"] == {
            "current": 2,
            "total": 2,
            "progress": 100.0,
        }


class TestAggregateProductsBatchTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_async_runner(self) -> None:
        """Celery-задача делегирует выполнение асинхронной реализации через asyncio.run."""
        with patch(
            "tasks.aggregation_tasks._aggregate_products_batch_async",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "total": 0,
                    "aggregated": 0,
                    "failed": 0,
                    "errors": [],
                }
            ),
        ) as mock_async:
            result = aggregate_products_batch.run(1, ["A"])

        assert result["success"] is True
        mock_async.assert_awaited_once()
