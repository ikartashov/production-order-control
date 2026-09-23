import copy
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.exceptions import ConflictError, NotFoundError
from data.models.batch import Batch
from data.models.work_center import WorkCenter
from domain.services.batch_service import BatchService
from tests.conftest import make_batch_dict, patch_batch_service_repos

# Создание партии


class TestCreateBatches:
    async def test_creates_single_batch(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        work_center: WorkCenter,
    ) -> None:
        """Создается партия без ошибок."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_or_create.return_value = work_center
        mock_batch_repo.get_by_number_and_date.return_value = None
        mock_batch_repo.create.return_value = batch
        mock_batch_repo.get_by_id_with_relations.return_value = batch

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            result = await service.create_batches([make_batch_dict()])

        assert len(result) == 1
        mock_batch_repo.create.assert_awaited_once()

    async def test_creates_multiple_batches(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        work_center: WorkCenter,
    ) -> None:
        """Три партии с разными номерами за один вызов."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_wc_repo.get_or_create.return_value = work_center
        mock_batch_repo.get_by_number_and_date.return_value = None
        mock_batch_repo.create.return_value = batch
        mock_batch_repo.get_by_id_with_relations.return_value = batch

        items = [make_batch_dict(batch_number=i) for i in range(1, 4)]

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            result = await service.create_batches(items)

        assert len(result) == 3
        assert mock_batch_repo.create.await_count == 3

    async def test_duplicate_in_db_raises_conflict(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """Ошибка ConflictError возникает,
        если в базе данных уже существует пакет с таким же номером и датой."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_number_and_date.return_value = batch

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            with pytest.raises(ConflictError):
                await service.create_batches([make_batch_dict()])

    async def test_duplicate_within_request_raises_conflict(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Ошибка ConflictError возникает,
        когда два элемента в одном запросе имеют
        одинаковые значения batch_number и date."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_number_and_date.return_value = None

        items = [make_batch_dict(), make_batch_dict()]

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            with pytest.raises(ConflictError):
                await service.create_batches(items)

    async def test_empty_list_returns_empty_result(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Пустой входной список возвращает пустой результат без вызова репозитория."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            result = await service.create_batches([])

        assert result == []
        mock_batch_repo.create.assert_not_awaited()


# Тесты get_batch


class TestGetBatch:
    """Тесты для BatchService.get_batch."""

    async def test_returns_batch_by_id(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """Существующая патрия возвращается корректно."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_id_with_relations.return_value = batch

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            result = await service.get_batch(1)

        assert result.id == 1
        mock_batch_repo.get_by_id_with_relations.assert_awaited_once_with(1)

    async def test_missing_id_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Исключение NotFoundError возникает, если пакет не существует."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_id_with_relations.return_value = None

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            with pytest.raises(NotFoundError):
                await service.get_batch(999)


# Тесты update_batch


class TestUpdateBatch:
    """Тесты для BatchService.update_batch."""

    async def test_closing_batch_sets_closed_at(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """Поле closed_at устанавливается, когда передается is_closed=True."""
        closed = copy.copy(batch)
        closed.is_closed = True
        closed.closed_at = datetime(2024, 1, 30, 20, 0, 0)

        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        # First call: fetch batch for update; second: fetch updated batch
        mock_batch_repo.get_by_id_with_relations.side_effect = [batch, closed]
        mock_batch_repo.update.return_value = closed

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            result = await service.update_batch(1, {"is_closed": True})

        assert result.is_closed is True
        assert result.closed_at is not None

    async def test_reopening_batch_clears_closed_at(
        self,
        mock_session: AsyncMock,
        closed_batch: Batch,
        batch: Batch,
    ) -> None:
        """Значение closed_at очищается,
        если для закрытого пакета передается параметр is_closed=False."""
        reopened = copy.copy(batch)
        reopened.is_closed = False
        reopened.closed_at = None

        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_id_with_relations.side_effect = [closed_batch, reopened]
        mock_batch_repo.update.return_value = reopened

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            result = await service.update_batch(1, {"is_closed": False})

        assert result.is_closed is False
        assert result.closed_at is None

    async def test_missing_batch_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Исключение NotFoundError возникает при обновлении несуществующего пакета."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_id_with_relations.return_value = None

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            with pytest.raises(NotFoundError):
                await service.update_batch(999, {"team": "New team"})

    async def test_update_passes_batch_object_to_repository(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """Метод обновления репозитория получает
        объект Batch в качестве первого аргумента."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_by_id_with_relations.return_value = batch
        mock_batch_repo.update.return_value = batch

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            await service.update_batch(1, {"team": "New team"})

        assert mock_batch_repo.update.call_args.args[0] is batch


# Тесты get_batches_list


class TestGetBatchesList:
    """Тесты для BatchService.get_batches_list."""

    async def test_returns_items_and_total(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """Список пакетов и общее количество возвращаются корректно."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_list.return_value = ([batch], 1)

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            batches, total = await service.get_batches_list({})

        assert total == 1
        assert len(batches) == 1
        assert batches[0].id == batch.id

    async def test_empty_db_returns_empty_list(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Пустая таблица пакета возвращает
        пустой список с нулевым итоговым значением."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_list.return_value = ([], 0)

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            batches, total = await service.get_batches_list({})

        assert total == 0
        assert batches == []

    async def test_filters_are_forwarded_to_repository(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Словарь фильтров распаковывается и
        передается в репозиторий в качестве аргументов `kwargs`."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_list.return_value = ([], 0)

        filters = {"is_closed": True, "batch_number": 22222, "offset": 0, "limit": 20}

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            await service.get_batches_list(filters)

        mock_batch_repo.get_list.assert_awaited_once_with(**filters)

    async def test_none_filters_are_forwarded_unchanged(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """Значения фильтров передаются в
        репозиторий как есть (репозиторий сам их обрабатывает)."""
        mock_batch_repo = AsyncMock()
        mock_wc_repo = AsyncMock()
        mock_batch_repo.get_list.return_value = ([], 0)

        filters = {"is_closed": None, "batch_number": None, "offset": 0, "limit": 20}

        with patch_batch_service_repos(mock_batch_repo, mock_wc_repo):
            service = BatchService(mock_session)
            await service.get_batches_list(filters)

        mock_batch_repo.get_list.assert_awaited_once_with(**filters)
