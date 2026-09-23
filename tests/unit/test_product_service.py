import copy
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.exceptions import ConflictError, NotFoundError, ValidationError
from data.models.batch import Batch
from data.models.product import Product
from domain.services.product_service import ProductService
from tests.conftest import make_product_dict, patch_product_service_repos

# Тесты add_products


class TestAddProducts:
    """Тесты для ProductService.add_products."""

    async def test_adds_products_successfully(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        product: Product,
    ) -> None:
        """Новые уникальные коды добавляются без ошибок."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_existing_codes.return_value = set()
        mock_product_repo.bulk_create.return_value = [product]

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            result = await service.add_products([make_product_dict("CODE001")])

        assert len(result) == 1
        mock_product_repo.bulk_create.assert_awaited_once()

    async def test_missing_batch_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """NotFoundError при добавлении в несуществующую партию."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = None

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(NotFoundError):
                await service.add_products([make_product_dict("CODE001")])

    async def test_closed_batch_raises_validation_error(
        self,
        mock_session: AsyncMock,
        closed_batch: Batch,
    ) -> None:
        """ValidationError при добавлении продукции в закрытую партию."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = closed_batch

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(ValidationError):
                await service.add_products([make_product_dict("CODE001")])

    async def test_existing_code_in_db_raises_conflict(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """ConflictError если unique_code уже существует в БД."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_existing_codes.return_value = {"CODE001"}

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(ConflictError):
                await service.add_products([make_product_dict("CODE001")])

    async def test_duplicate_code_in_request_raises_conflict(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """ConflictError если один unique_code встречается дважды в одном запросе."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_existing_codes.return_value = set()

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(ConflictError):
                await service.add_products(
                    [make_product_dict("CODE001"), make_product_dict("CODE001")]
                )

    async def test_bulk_create_receives_correct_code_count(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        product: Product,
    ) -> None:
        """bulk_create вызывается один раз с тем же количеством кодов, что и в запросе."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_existing_codes.return_value = set()
        mock_product_repo.bulk_create.return_value = [product, product, product]

        items = [make_product_dict(f"CODE00{i}") for i in range(1, 4)]

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            await service.add_products(items)

        mock_product_repo.bulk_create.assert_awaited_once()
        _, codes = mock_product_repo.bulk_create.call_args.args
        assert len(codes) == 3

    async def test_invalidates_batch_detail_cache_after_adding_products(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        product: Product,
    ) -> None:
        """Регрессия Fix 1: add_products должен инвалидировать batch_detail

        и dashboard_stats затронутой партии — раньше этого не происходило,
        и GET /batches/{id} мог до 10 минут отдавать партию без только что
        добавленной продукции.
        """
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_existing_codes.return_value = set()
        mock_product_repo.bulk_create.return_value = [product]

        mock_cache_delete = AsyncMock()
        mock_cache_delete_pattern = AsyncMock()

        with patch_product_service_repos(
            mock_product_repo,
            mock_batch_repo,
            mock_cache_delete=mock_cache_delete,
            mock_cache_delete_pattern=mock_cache_delete_pattern,
        ):
            service = ProductService(mock_session)
            await service.add_products([make_product_dict("CODE001", batch_id=1)])

        deleted_keys = {call.args[0] for call in mock_cache_delete.await_args_list}
        assert "batch_detail:1" in deleted_keys
        assert "dashboard_stats" in deleted_keys
        # add_products не меняет поля BatchListItem (is_closed/nomenclature/...),
        # поэтому список партий инвалидировать не нужно.
        mock_cache_delete_pattern.assert_not_awaited()

    async def test_invalidates_caches_for_every_batch_touched(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        product: Product,
    ) -> None:
        """Один вызов add_products может добавить продукцию сразу в несколько

        партий (сгруппировано по batch_id) — все затронутые batch_id должны
        получить инвалидацию своего batch_detail.
        """
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_existing_codes.return_value = set()
        mock_product_repo.bulk_create.return_value = [product]

        mock_cache_delete = AsyncMock()

        items = [
            make_product_dict("CODE001", batch_id=1),
            make_product_dict("CODE002", batch_id=2),
        ]

        with patch_product_service_repos(
            mock_product_repo, mock_batch_repo, mock_cache_delete=mock_cache_delete
        ):
            service = ProductService(mock_session)
            await service.add_products(items)

        deleted_keys = {call.args[0] for call in mock_cache_delete.await_args_list}
        assert "batch_detail:1" in deleted_keys
        assert "batch_detail:2" in deleted_keys
        assert "dashboard_stats" in deleted_keys


# Тесты aggregate_products


class TestAggregateProducts:
    """Тесты для ProductService.aggregate_products."""

    async def test_aggregates_products_successfully(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        product: Product,
    ) -> None:
        """Продукция из открытой партии агрегируется без ошибок."""
        aggregated = copy.copy(product)
        aggregated.is_aggregated = True
        aggregated.aggregated_at = datetime(2024, 1, 30, 12, 0, 0)

        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_by_codes_and_batch.return_value = [product]
        mock_product_repo.aggregate_products.return_value = [aggregated]

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            result = await service.aggregate_products(1, ["CODE001"])

        assert len(result) == 1
        assert result[0].is_aggregated is True
        mock_product_repo.aggregate_products.assert_awaited_once_with([product.id])

    async def test_missing_batch_raises_not_found(
        self,
        mock_session: AsyncMock,
    ) -> None:
        """NotFoundError если партия не существует."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = None

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(NotFoundError):
                await service.aggregate_products(999, ["CODE001"])

    async def test_closed_batch_raises_validation_error(
        self,
        mock_session: AsyncMock,
        closed_batch: Batch,
    ) -> None:
        """ValidationError при агрегации в закрытой партии."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = closed_batch

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(ValidationError):
                await service.aggregate_products(1, ["CODE001"])

    async def test_missing_code_raises_not_found(
        self,
        mock_session: AsyncMock,
        batch: Batch,
    ) -> None:
        """NotFoundError если код не найден в партии."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_by_codes_and_batch.return_value = []

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(NotFoundError):
                await service.aggregate_products(1, ["MISSING"])

    async def test_already_aggregated_raises_conflict(
        self,
        mock_session: AsyncMock,
        batch: Batch,
        aggregated_product: Product,
    ) -> None:
        """ConflictError при попытке повторной агрегации продукции."""
        mock_product_repo = AsyncMock()
        mock_batch_repo = AsyncMock()
        mock_batch_repo.get_by_id.return_value = batch
        mock_product_repo.get_by_codes_and_batch.return_value = [aggregated_product]

        with patch_product_service_repos(mock_product_repo, mock_batch_repo):
            service = ProductService(mock_session)
            with pytest.raises(ConflictError):
                await service.aggregate_products(1, ["CODE001"])
