from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from core.exceptions import NotFoundError
from data.models.work_center import WorkCenter
from domain.services.work_center_service import WorkCenterService

WC_REPO_PATH = "domain.services.work_center_service.WorkCenterRepository"


@contextmanager
def patch_work_center_service_repo(mock_wc_repo: AsyncMock) -> Iterator[None]:
    """Патчит репозиторий WorkCenterService."""
    with patch(WC_REPO_PATH, return_value=mock_wc_repo):
        yield


# Тесты get_work_center


class TestGetWorkCenter:
    """Тесты для WorkCenterService.get_work_center."""

    async def test_returns_work_center_by_id(
        self,
        mock_session: AsyncMock,
        work_center: WorkCenter,
    ) -> None:
        """Существующий рабочий центр возвращается корректно."""
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_by_id.return_value = work_center

        with patch_work_center_service_repo(mock_wc_repo):
            service = WorkCenterService(mock_session)
            result = await service.get_work_center(1)

        assert result.id == 1
        mock_wc_repo.get_by_id.assert_awaited_once_with(1)

    async def test_missing_id_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Исключение NotFoundError возникает, если рабочий центр не найден."""
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_by_id.return_value = None

        with patch_work_center_service_repo(mock_wc_repo):
            service = WorkCenterService(mock_session)
            with pytest.raises(NotFoundError):
                await service.get_work_center(999)


# Тесты get_work_centers_list


class TestGetWorkCentersList:
    """Тесты для WorkCenterService.get_work_centers_list."""

    async def test_returns_items_and_total(
        self,
        mock_session: AsyncMock,
        work_center: WorkCenter,
    ) -> None:
        """Список рабочих центров и общее количество возвращаются корректно."""
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_list.return_value = ([work_center], 1)

        with patch_work_center_service_repo(mock_wc_repo):
            service = WorkCenterService(mock_session)
            work_centers, total = await service.get_work_centers_list()

        assert total == 1
        assert len(work_centers) == 1
        assert work_centers[0].id == work_center.id

    async def test_empty_db_returns_empty_list(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Пустая таблица рабочих центров возвращает
        пустой список с нулевым итоговым значением."""
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_list.return_value = ([], 0)

        with patch_work_center_service_repo(mock_wc_repo):
            service = WorkCenterService(mock_session)
            work_centers, total = await service.get_work_centers_list()

        assert total == 0
        assert work_centers == []

    async def test_offset_and_limit_are_forwarded_to_repository(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Параметры offset/limit передаются в репозиторий как есть."""
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_list.return_value = ([], 0)

        with patch_work_center_service_repo(mock_wc_repo):
            service = WorkCenterService(mock_session)
            await service.get_work_centers_list(offset=10, limit=50)

        mock_wc_repo.get_list.assert_awaited_once_with(offset=10, limit=50)
