from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from tasks.scheduled_tasks import (
    _auto_close_expired_batches_async,
    _cleanup_old_files_sync,
    _retry_failed_webhooks_async,
    _update_cached_statistics_async,
    auto_close_expired_batches,
    cleanup_old_files,
    retry_failed_webhooks,
    update_cached_statistics,
)


@asynccontextmanager
async def _fake_session() -> Any:
    yield AsyncMock()


class TestAutoCloseExpiredBatchesAsync:
    """Тесты автозакрытия партий с истёкшей сменой.

    Раньше каждая просроченная партия закрывалась через полный
    ``BatchService.update_batch`` (repo UPDATE + cache_delete_pattern +
    2×cache_delete + re-fetch SELECT на партию, всё сериализовано per-batch).
    Теперь партии закрываются одним bulk UPDATE
    (``BatchRepository.bulk_close``), а событие ``batch_closed`` (с точной
    per-batch статистикой) по-прежнему рассылается индивидуально на каждую
    партию — это обязательное поведение для подписчиков, см.
    CRITICAL CONSTRAINT в задаче. Кеш инвалидируется один раз для
    паттерна/дашборда и по одному разу на партию для точечного
    batch_detail-ключа.
    """

    async def test_expired_batches_get_closed(self) -> None:
        """Просроченные открытые партии закрываются одним bulk_close."""
        expired_1 = MagicMock(id=1, batch_number=101)
        expired_2 = MagicMock(id=2, batch_number=102)

        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_expired_open_batches.return_value = [expired_1, expired_2]

        mock_product_repo = AsyncMock()
        mock_product_repo.get_batch_stats.side_effect = [(10, 5), (20, 20)]

        mock_webhook_service = AsyncMock()

        mock_cache_delete = AsyncMock()
        mock_cache_delete_pattern = AsyncMock()

        with (
            patch("tasks.scheduled_tasks.get_session", _fake_session),
            patch(
                "tasks.scheduled_tasks.BatchRepository", return_value=mock_batch_repo
            ),
            patch(
                "tasks.scheduled_tasks.ProductRepository",
                return_value=mock_product_repo,
            ),
            patch(
                "tasks.scheduled_tasks.WebhookService",
                return_value=mock_webhook_service,
            ),
            patch("tasks.scheduled_tasks.cache_delete", mock_cache_delete),
            patch(
                "tasks.scheduled_tasks.cache_delete_pattern",
                mock_cache_delete_pattern,
            ),
        ):
            result = await _auto_close_expired_batches_async()

        assert result == {"success": True, "closed": 2, "batch_ids": [1, 2]}

        # (a) bulk_close вызван один раз со всеми ID просроченных партий.
        mock_batch_repo.bulk_close.assert_awaited_once_with([1, 2])

        # (b) dispatch_event("batch_closed", ...) вызван по разу на партию
        # с корректной per-batch статистикой.
        assert mock_webhook_service.dispatch_event.await_count == 2
        mock_webhook_service.dispatch_event.assert_any_await(
            "batch_closed",
            {
                "id": 1,
                "batch_number": 101,
                "closed_at": ANY,
                "statistics": {
                    "total_products": 10,
                    "aggregated": 5,
                    "aggregation_rate": 50.0,
                },
            },
        )
        mock_webhook_service.dispatch_event.assert_any_await(
            "batch_closed",
            {
                "id": 2,
                "batch_number": 102,
                "closed_at": ANY,
                "statistics": {
                    "total_products": 20,
                    "aggregated": 20,
                    "aggregation_rate": 100.0,
                },
            },
        )

        # (c) кеш: паттерн и дашборд по одному разу, batch_detail — по разу
        # на партию (дешёвый точечный ключ, N раз это ожидаемо и ок).
        mock_cache_delete_pattern.assert_awaited_once_with("batches_list:*")
        assert mock_cache_delete.await_count == 3
        mock_cache_delete.assert_any_await("dashboard_stats")
        mock_cache_delete.assert_any_await("batch_detail:1")
        mock_cache_delete.assert_any_await("batch_detail:2")

    async def test_no_expired_batches_closes_nothing(self) -> None:
        """Если репозиторий не вернул просроченных партий, ничего не закрывается."""
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_expired_open_batches.return_value = []

        mock_product_repo = AsyncMock()
        mock_webhook_service = AsyncMock()
        mock_cache_delete = AsyncMock()
        mock_cache_delete_pattern = AsyncMock()

        with (
            patch("tasks.scheduled_tasks.get_session", _fake_session),
            patch(
                "tasks.scheduled_tasks.BatchRepository", return_value=mock_batch_repo
            ),
            patch(
                "tasks.scheduled_tasks.ProductRepository",
                return_value=mock_product_repo,
            ),
            patch(
                "tasks.scheduled_tasks.WebhookService",
                return_value=mock_webhook_service,
            ),
            patch("tasks.scheduled_tasks.cache_delete", mock_cache_delete),
            patch(
                "tasks.scheduled_tasks.cache_delete_pattern",
                mock_cache_delete_pattern,
            ),
        ):
            result = await _auto_close_expired_batches_async()

        assert result == {"success": True, "closed": 0, "batch_ids": []}
        mock_batch_repo.bulk_close.assert_not_awaited()
        mock_product_repo.get_batch_stats.assert_not_awaited()
        mock_webhook_service.dispatch_event.assert_not_awaited()
        mock_cache_delete.assert_not_awaited()
        mock_cache_delete_pattern.assert_not_awaited()


class TestAutoCloseExpiredBatchesTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_async_runner(self) -> None:
        with patch(
            "tasks.scheduled_tasks._auto_close_expired_batches_async",
            new=AsyncMock(return_value={"success": True, "closed": 0, "batch_ids": []}),
        ) as mock_async:
            result = auto_close_expired_batches.run()

        assert result["success"] is True
        mock_async.assert_awaited_once()


class TestCleanupOldFilesSync:
    """Тесты очистки устаревших файлов в MinIO."""

    def test_old_files_get_deleted_recent_files_dont(self) -> None:
        now = datetime.now(UTC)
        old_file = MagicMock(
            object_name="old.xlsx", last_modified=now - timedelta(days=40)
        )
        recent_file = MagicMock(
            object_name="recent.xlsx", last_modified=now - timedelta(days=1)
        )

        mock_minio = MagicMock()
        mock_minio.list_files.return_value = [old_file, recent_file]

        with (
            patch("tasks.scheduled_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.scheduled_tasks.BUCKETS", ["reports"]),
        ):
            result = _cleanup_old_files_sync()

        assert result == {
            "success": True,
            "deleted": 1,
            "files": ["reports/old.xlsx"],
        }
        mock_minio.delete_file.assert_called_once_with("reports", "old.xlsx")

    def test_no_old_files_deletes_nothing(self) -> None:
        now = datetime.now(UTC)
        recent_file = MagicMock(
            object_name="recent.xlsx", last_modified=now - timedelta(days=1)
        )

        mock_minio = MagicMock()
        mock_minio.list_files.return_value = [recent_file]

        with (
            patch("tasks.scheduled_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.scheduled_tasks.BUCKETS", ["reports"]),
        ):
            result = _cleanup_old_files_sync()

        assert result == {"success": True, "deleted": 0, "files": []}
        mock_minio.delete_file.assert_not_called()

    def test_checks_all_buckets(self) -> None:
        now = datetime.now(UTC)
        old_file = MagicMock(
            object_name="old.csv", last_modified=now - timedelta(days=31)
        )

        mock_minio = MagicMock()
        mock_minio.list_files.return_value = [old_file]

        with (
            patch("tasks.scheduled_tasks.MinIOService", return_value=mock_minio),
            patch("tasks.scheduled_tasks.BUCKETS", ["reports", "exports", "imports"]),
        ):
            result = _cleanup_old_files_sync()

        assert result["deleted"] == 3
        assert mock_minio.list_files.call_count == 3


class TestCleanupOldFilesTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_sync_runner(self) -> None:
        with patch(
            "tasks.scheduled_tasks._cleanup_old_files_sync",
            return_value={"success": True, "deleted": 0, "files": []},
        ) as mock_sync:
            result = cleanup_old_files.run()

        assert result["success"] is True
        mock_sync.assert_called_once()


class TestUpdateCachedStatisticsAsync:
    """Тесты пересчёта и кеширования статистики дашборда.

    Вычисление делегировано общей функции
    ``analytics_service.compute_dashboard_stats`` (единый источник правды
    для полей дашборда, см. ``domain/services/analytics_service.py``),
    поэтому здесь мокается она сама, а не отдельные SQL-запросы.
    """

    async def test_stats_cached_with_expected_keys(self) -> None:
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_with_mock() -> Any:
            yield mock_session

        computed_stats = {
            "total_batches": 10,
            "active_batches": 4,
            "closed_batches": 6,
            "total_products": 100,
            "aggregated_products": 60,
            "aggregation_rate": 60.0,
            "cached_at": "2024-01-30T12:00:00+00:00",
        }
        mock_cache_set = AsyncMock()
        mock_compute = AsyncMock(return_value=computed_stats)

        with (
            patch("tasks.scheduled_tasks.get_session", fake_session_with_mock),
            patch("tasks.scheduled_tasks.cache_set", mock_cache_set),
            patch("tasks.scheduled_tasks.compute_dashboard_stats", mock_compute),
        ):
            result = await _update_cached_statistics_async()

        stats = result["stats"]
        assert stats == computed_stats
        mock_compute.assert_awaited_once_with(mock_session)

        mock_cache_set.assert_awaited_once()
        args, kwargs = mock_cache_set.await_args
        assert args[0] == "dashboard_stats"
        assert args[1] == stats
        assert kwargs["ttl"] == 300

    async def test_cached_stats_validate_against_dashboard_summary_schema(
        self,
    ) -> None:
        """Регрессионный тест на исходный баг: раньше scheduled_tasks
        писал в кеш словарь без ``closed_batches``, что валилось при
        валидации DashboardSummary на чтении. compute_dashboard_stats —
        общий источник правды, поэтому кешируемый словарь всегда содержит
        это поле."""
        from api.v1.schemas.analytics import DashboardSummary

        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_with_mock() -> Any:
            yield mock_session

        computed_stats = {
            "total_batches": 10,
            "active_batches": 4,
            "closed_batches": 6,
            "total_products": 100,
            "aggregated_products": 60,
            "aggregation_rate": 60.0,
            "cached_at": "2024-01-30T12:00:00+00:00",
            "today": {
                "batches_created": 1,
                "batches_closed": 1,
                "products_added": 10,
                "products_aggregated": 8,
            },
            "by_shift": {
                "1 смена": {"batches": 6, "products": 60, "aggregated": 36},
            },
            "top_work_centers": [
                {
                    "id": "RC-001",
                    "name": "Цех №1",
                    "batches_count": 10,
                    "products_count": 100,
                    "aggregation_rate": 60.0,
                },
            ],
        }

        with (
            patch("tasks.scheduled_tasks.get_session", fake_session_with_mock),
            patch("tasks.scheduled_tasks.cache_set", new=AsyncMock()),
            patch(
                "tasks.scheduled_tasks.compute_dashboard_stats",
                AsyncMock(return_value=computed_stats),
            ),
        ):
            result = await _update_cached_statistics_async()

        DashboardSummary.model_validate(result["stats"])

    async def test_zero_products_gives_zero_rate(self) -> None:
        mock_session = AsyncMock()

        @asynccontextmanager
        async def fake_session_with_mock() -> Any:
            yield mock_session

        computed_stats = {
            "total_batches": 0,
            "active_batches": 0,
            "closed_batches": 0,
            "total_products": 0,
            "aggregated_products": 0,
            "aggregation_rate": 0.0,
            "cached_at": "2024-01-30T12:00:00+00:00",
        }

        with (
            patch("tasks.scheduled_tasks.get_session", fake_session_with_mock),
            patch("tasks.scheduled_tasks.cache_set", new=AsyncMock()),
            patch(
                "tasks.scheduled_tasks.compute_dashboard_stats",
                AsyncMock(return_value=computed_stats),
            ),
        ):
            result = await _update_cached_statistics_async()

        assert result["stats"]["aggregation_rate"] == 0.0


class TestUpdateCachedStatisticsTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_async_runner(self) -> None:
        with patch(
            "tasks.scheduled_tasks._update_cached_statistics_async",
            new=AsyncMock(return_value={"success": True, "stats": {}}),
        ) as mock_async:
            result = update_cached_statistics.run()

        assert result["success"] is True
        mock_async.assert_awaited_once()


class TestRetryFailedWebhooksAsync:
    """Тесты повторной постановки в очередь неудачных доставок вебхуков."""

    async def test_stale_failed_deliveries_get_retried(self) -> None:
        delivery_1 = MagicMock(id=11)
        delivery_2 = MagicMock(id=12)

        mock_webhook_repo = AsyncMock()
        mock_webhook_repo.claim_deliveries_to_retry.return_value = [
            delivery_1,
            delivery_2,
        ]

        with (
            patch("tasks.scheduled_tasks.get_session", _fake_session),
            patch(
                "tasks.scheduled_tasks.WebhookRepository",
                return_value=mock_webhook_repo,
            ),
            patch("tasks.scheduled_tasks.send_webhook") as mock_send_webhook,
        ):
            result = await _retry_failed_webhooks_async()

        assert result == {"success": True, "retried": 2}
        mock_send_webhook.delay.assert_any_call(11)
        mock_send_webhook.delay.assert_any_call(12)
        assert mock_send_webhook.delay.call_count == 2

    async def test_uses_atomic_claim_not_plain_select(self) -> None:
        """Регрессионный тест бага дублирующей отправки: задача должна
        использовать атомарный ``claim_deliveries_to_retry`` (который сразу
        переводит статус в ``retrying``), а не старый read-only
        ``get_deliveries_to_retry``, иначе повторный sweep до завершения
        ранее поставленной ``send_webhook`` мог бы выбрать ту же доставку
        снова и отправить вебхук дважды."""
        delivery = MagicMock(id=11)
        mock_webhook_repo = AsyncMock()
        mock_webhook_repo.claim_deliveries_to_retry.return_value = [delivery]

        with (
            patch("tasks.scheduled_tasks.get_session", _fake_session),
            patch(
                "tasks.scheduled_tasks.WebhookRepository",
                return_value=mock_webhook_repo,
            ),
            patch("tasks.scheduled_tasks.send_webhook") as mock_send_webhook,
        ):
            result = await _retry_failed_webhooks_async()

        assert result == {"success": True, "retried": 1}
        mock_webhook_repo.claim_deliveries_to_retry.assert_awaited_once()
        mock_webhook_repo.get_deliveries_to_retry.assert_not_awaited()
        mock_send_webhook.delay.assert_called_once_with(11)

    async def test_no_deliveries_to_retry(self) -> None:
        mock_webhook_repo = AsyncMock()
        mock_webhook_repo.claim_deliveries_to_retry.return_value = []

        with (
            patch("tasks.scheduled_tasks.get_session", _fake_session),
            patch(
                "tasks.scheduled_tasks.WebhookRepository",
                return_value=mock_webhook_repo,
            ),
            patch("tasks.scheduled_tasks.send_webhook") as mock_send_webhook,
        ):
            result = await _retry_failed_webhooks_async()

        assert result == {"success": True, "retried": 0}
        mock_send_webhook.delay.assert_not_called()


class TestRetryFailedWebhooksTask:
    """Тест синхронной Celery-обёртки."""

    def test_run_invokes_async_runner(self) -> None:
        with patch(
            "tasks.scheduled_tasks._retry_failed_webhooks_async",
            new=AsyncMock(return_value={"success": True, "retried": 0}),
        ) as mock_async:
            result = retry_failed_webhooks.run()

        assert result["success"] is True
        mock_async.assert_awaited_once()
