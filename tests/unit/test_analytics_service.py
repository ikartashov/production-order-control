from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from core.exceptions import NotFoundError
from data.models.batch import Batch
from domain.services.analytics_service import AnalyticsService

BATCH_REPO_PATH = "domain.services.analytics_service.BatchRepository"
PRODUCT_REPO_PATH = "domain.services.analytics_service.ProductRepository"
BATCH_SERVICE_PATH = "domain.services.analytics_service.BatchService"
CACHE_GET_PATH = "domain.services.analytics_service.cache_get"
CACHE_SET_PATH = "domain.services.analytics_service.cache_set"


@contextmanager
def patch_analytics_service_repos(
    mock_batch_repo: AsyncMock | None = None,
    mock_product_repo: AsyncMock | None = None,
    mock_batch_service: AsyncMock | None = None,
) -> Iterator[None]:
    """Патчит репозитории и BatchService, используемые AnalyticsService.

    Локальный контекстный менеджер (по аналогии с
    ``patch_work_center_service_repo`` в ``test_work_center_service.py``) —
    не редактирует общий ``tests/conftest.py``.
    """
    mock_batch_repo = mock_batch_repo or AsyncMock()
    mock_product_repo = mock_product_repo or AsyncMock()
    mock_batch_service = mock_batch_service or AsyncMock()
    with (
        patch(BATCH_REPO_PATH, return_value=mock_batch_repo),
        patch(PRODUCT_REPO_PATH, return_value=mock_product_repo),
        patch(BATCH_SERVICE_PATH, return_value=mock_batch_service),
    ):
        yield


def make_tz_batch(
    *,
    batch_id: int = 1,
    batch_number: int = 22222,
    is_closed: bool = False,
    shift_start: datetime | None = None,
    shift_end: datetime | None = None,
) -> Batch:
    """Партия с timezone-aware shift_start/shift_end (как в БД: DateTime(timezone=True)).

    Общая фикстура ``batch`` из ``tests/conftest.py`` использует naive
    datetime, что несовместимо с арифметикой относительно
    ``datetime.now(UTC)`` в ``AnalyticsService.get_batch_statistics`` —
    поэтому для этого файла нужна собственная tz-aware фабрика.
    """
    b = Batch()
    b.id = batch_id
    b.batch_number = batch_number
    b.batch_date = date(2024, 1, 30)
    b.is_closed = is_closed
    b.shift_start = shift_start or datetime(2024, 1, 30, 8, 0, tzinfo=UTC)
    b.shift_end = shift_end or datetime(2024, 1, 30, 20, 0, tzinfo=UTC)
    return b


# Тесты get_dashboard_stats


class TestGetDashboardStats:
    """Тесты для AnalyticsService.get_dashboard_stats."""

    async def test_cache_hit_returns_cached_without_querying_repos(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """При попадании в кеш возвращается закешированное значение,
        репозитории при этом не вызываются."""
        cached = {
            "total_batches": 5,
            "active_batches": 2,
            "closed_batches": 3,
            "total_products": 50,
            "aggregated_products": 20,
            "aggregation_rate": 40.0,
            "cached_at": "2024-01-30T12:00:00+00:00",
        }
        mock_batch_repo = AsyncMock()
        mock_product_repo = AsyncMock()

        with (
            patch_analytics_service_repos(mock_batch_repo, mock_product_repo),
            patch(CACHE_GET_PATH, AsyncMock(return_value=cached)) as mock_cache_get,
            patch(CACHE_SET_PATH, AsyncMock()) as mock_cache_set,
        ):
            service = AnalyticsService(mock_session)
            result = await service.get_dashboard_stats()

        assert result == cached
        mock_cache_get.assert_awaited_once()
        mock_cache_set.assert_not_awaited()
        mock_batch_repo.get_summary_counts.assert_not_awaited()
        mock_product_repo.get_global_stats.assert_not_awaited()

    async def test_cache_miss_computes_and_caches(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """При промахе кеша статистика считается из репозиториев
        и сохраняется в кеш."""
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_summary_counts.return_value = (10, 4)
        mock_product_repo = AsyncMock()
        mock_product_repo.get_global_stats.return_value = (100, 60)

        with (
            patch_analytics_service_repos(mock_batch_repo, mock_product_repo),
            patch(CACHE_GET_PATH, AsyncMock(return_value=None)),
            patch(CACHE_SET_PATH, AsyncMock()) as mock_cache_set,
        ):
            service = AnalyticsService(mock_session)
            result = await service.get_dashboard_stats()

        assert result["total_batches"] == 10
        assert result["active_batches"] == 4
        assert result["closed_batches"] == 6
        assert result["total_products"] == 100
        assert result["aggregated_products"] == 60
        assert result["aggregation_rate"] == 60.0
        assert "cached_at" in result

        mock_cache_set.assert_awaited_once()
        cache_args = mock_cache_set.call_args
        assert cache_args.args[0] == "dashboard_stats"
        assert cache_args.args[1] == result
        assert cache_args.kwargs["ttl"] == 300

    async def test_cache_miss_with_zero_products_avoids_division_by_zero(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Пустая база продукции не вызывает деления на ноль."""
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_summary_counts.return_value = (0, 0)
        mock_product_repo = AsyncMock()
        mock_product_repo.get_global_stats.return_value = (0, 0)

        with (
            patch_analytics_service_repos(mock_batch_repo, mock_product_repo),
            patch(CACHE_GET_PATH, AsyncMock(return_value=None)),
            patch(CACHE_SET_PATH, AsyncMock()),
        ):
            service = AnalyticsService(mock_session)
            result = await service.get_dashboard_stats()

        assert result["aggregation_rate"] == 0.0


# Тесты get_batch_statistics


class TestGetBatchStatistics:
    """Тесты для AnalyticsService.get_batch_statistics."""

    async def test_returns_statistics_for_open_batch(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Статистика и таймлайн корректно считаются для открытой партии
        в середине смены."""
        now = datetime.now(UTC)
        tz_batch = make_tz_batch(
            shift_start=now - timedelta(hours=4),
            shift_end=now + timedelta(hours=4),
        )
        mock_batch_service = AsyncMock()
        mock_batch_service.get_batch.return_value = tz_batch
        mock_product_repo = AsyncMock()
        mock_product_repo.get_batch_stats.return_value = (100, 50)

        with patch_analytics_service_repos(
            mock_product_repo=mock_product_repo,
            mock_batch_service=mock_batch_service,
        ):
            service = AnalyticsService(mock_session)
            result = await service.get_batch_statistics(1)

        mock_batch_service.get_batch.assert_awaited_once_with(1)
        assert result["batch_info"] == {
            "id": 1,
            "batch_number": 22222,
            "batch_date": date(2024, 1, 30),
            "is_closed": False,
        }
        assert result["production_stats"] == {
            "total_products": 100,
            "aggregated": 50,
            "remaining": 50,
            "aggregation_rate": 50.0,
        }
        assert result["timeline"]["shift_duration_hours"] == pytest.approx(
            8.0, abs=0.01
        )
        assert result["timeline"]["elapsed_hours"] == pytest.approx(4.0, abs=0.05)
        assert result["timeline"]["products_per_hour"] > 0
        assert result["timeline"]["estimated_completion"] is not None

    async def test_completed_batch_estimates_shift_end(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Если весь объем агрегирован, прогноз завершения — конец смены."""
        now = datetime.now(UTC)
        shift_end = now + timedelta(hours=2)
        tz_batch = make_tz_batch(
            shift_start=now - timedelta(hours=6),
            shift_end=shift_end,
        )
        mock_batch_service = AsyncMock()
        mock_batch_service.get_batch.return_value = tz_batch
        mock_product_repo = AsyncMock()
        mock_product_repo.get_batch_stats.return_value = (100, 100)

        with patch_analytics_service_repos(
            mock_product_repo=mock_product_repo,
            mock_batch_service=mock_batch_service,
        ):
            service = AnalyticsService(mock_session)
            result = await service.get_batch_statistics(1)

        assert result["production_stats"]["remaining"] == 0
        assert result["timeline"]["estimated_completion"] == shift_end.isoformat()

    async def test_missing_batch_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Исключение NotFoundError пробрасывается из BatchService.get_batch."""
        mock_batch_service = AsyncMock()
        mock_batch_service.get_batch.side_effect = NotFoundError(
            "Партия с id=999 не найдена"
        )

        with patch_analytics_service_repos(mock_batch_service=mock_batch_service):
            service = AnalyticsService(mock_session)
            with pytest.raises(NotFoundError):
                await service.get_batch_statistics(999)


# Тесты compare_batches


class TestCompareBatches:
    """Тесты для AnalyticsService.compare_batches.

    С исправлением N+1 сравнение больше не ходит через
    ``BatchService.get_batch``/``ProductRepository.get_batch_stats`` в
    цикле — вместо этого один батч-запрос
    ``BatchRepository.get_by_ids`` и один агрегатный запрос
    ``ProductRepository.get_stats_for_batches``."""

    async def test_compares_multiple_batches_and_computes_averages(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Список сравнения и средние показатели считаются корректно
        для нескольких валидных партий."""
        t0 = datetime(2024, 1, 30, 8, 0, tzinfo=UTC)
        batch1 = make_tz_batch(
            batch_id=1,
            batch_number=111,
            shift_start=t0,
            shift_end=t0 + timedelta(hours=10),
        )
        batch2 = make_tz_batch(
            batch_id=2,
            batch_number=222,
            shift_start=t0,
            shift_end=t0 + timedelta(hours=20),
        )

        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_ids.return_value = [batch1, batch2]
        mock_product_repo = AsyncMock()
        mock_product_repo.get_stats_for_batches.return_value = {
            1: (100, 50),
            2: (200, 100),
        }

        with patch_analytics_service_repos(
            mock_batch_repo=mock_batch_repo,
            mock_product_repo=mock_product_repo,
        ):
            service = AnalyticsService(mock_session)
            result = await service.compare_batches([1, 2])

        mock_batch_repo.get_by_ids.assert_awaited_once_with([1, 2])
        mock_product_repo.get_stats_for_batches.assert_awaited_once_with([1, 2])

        assert result["comparison"] == [
            {
                "batch_id": 1,
                "batch_number": 111,
                "total_products": 100,
                "aggregated": 50,
                "rate": 50.0,
                "duration_hours": 10.0,
                "products_per_hour": 5.0,
            },
            {
                "batch_id": 2,
                "batch_number": 222,
                "total_products": 200,
                "aggregated": 100,
                "rate": 50.0,
                "duration_hours": 20.0,
                "products_per_hour": 5.0,
            },
        ]
        assert result["average"] == {
            "aggregation_rate": 50.0,
            "products_per_hour": 5.0,
        }

    async def test_missing_batch_id_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """NotFoundError пробрасывается, если один из batch_ids не существует
        (осознанный выбор — явная ошибка вместо тихого пропуска)."""
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_ids.return_value = []

        with patch_analytics_service_repos(mock_batch_repo=mock_batch_repo):
            service = AnalyticsService(mock_session)
            with pytest.raises(NotFoundError):
                await service.compare_batches([999])

    async def test_partially_missing_batch_ids_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Если найдена только часть партий, всё равно поднимается
        NotFoundError (для первого отсутствующего id по порядку запроса)."""
        batch1 = make_tz_batch(batch_id=1)
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_ids.return_value = [batch1]
        mock_product_repo = AsyncMock()

        with patch_analytics_service_repos(
            mock_batch_repo=mock_batch_repo,
            mock_product_repo=mock_product_repo,
        ):
            service = AnalyticsService(mock_session)
            with pytest.raises(NotFoundError):
                await service.compare_batches([1, 999])

        mock_product_repo.get_stats_for_batches.assert_not_awaited()

    async def test_empty_batch_ids_returns_empty_comparison(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Пустой список ID возвращает пустое сравнение и нулевые средние
        значения без обращения к репозиториям."""
        mock_batch_repo = AsyncMock()
        mock_product_repo = AsyncMock()

        with patch_analytics_service_repos(
            mock_batch_repo=mock_batch_repo,
            mock_product_repo=mock_product_repo,
        ):
            service = AnalyticsService(mock_session)
            result = await service.compare_batches([])

        assert result == {
            "comparison": [],
            "average": {"aggregation_rate": 0.0, "products_per_hour": 0.0},
        }
        mock_batch_repo.get_by_ids.assert_not_awaited()
        mock_product_repo.get_stats_for_batches.assert_not_awaited()
