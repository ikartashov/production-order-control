from itertools import groupby
from operator import itemgetter
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError, ValidationError
from data.models.batch import Batch
from data.models.product import Product
from data.repositories.batch_repository import BatchRepository
from data.repositories.product_repository import ProductRepository
from domain.services.batch_service import invalidate_batch_caches
from domain.services.webhook_service import WebhookService


class ProductService:
    """Сервис управления продукцией."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._product_repo = ProductRepository(Product, session)
        self._batch_repo = BatchRepository(Batch, session)

    async def add_products(self, items: list[dict[str, Any]]) -> list[Product]:
        """
        Атомарно добавить список продукции.

        Проверяет:
        - Батч существует
        - Батч не закрыт
        - Нет дублей unique_code внутри запроса
        - Нет дублей unique_code в БД
        """
        # Группируем по batch_id
        batch_ids = {item["batch_id"] for item in items}

        for batch_id in batch_ids:
            batch = await self._batch_repo.get_by_id(batch_id)
            if batch is None:
                raise NotFoundError(f"Партия с id={batch_id} не найдена")
            if batch.is_closed:
                raise ValidationError(
                    f"Партия id={batch_id} закрыта — добавление продукции невозможно"
                )

        # Проверяем дубли внутри запроса
        codes = [item["unique_code"] for item in items]
        if len(codes) != len(set(codes)):
            raise ConflictError("В запросе есть дублирующиеся unique_code")

        # Проверяем дубли в БД
        existing = await self._product_repo.get_existing_codes(codes)
        if existing:
            raise ConflictError(
                f"Следующие коды уже существуют: {', '.join(sorted(existing))}"
            )

        # Создаём — группируем по batch_id для bulk_create

        items_sorted = sorted(items, key=itemgetter("batch_id"))
        all_products: list[Product] = []

        for batch_id, group in groupby(items_sorted, key=itemgetter("batch_id")):
            codes_for_batch = [item["unique_code"] for item in group]
            products = await self._product_repo.bulk_create(batch_id, codes_for_batch)
            all_products.extend(products)
            logger.info(
                "Добавлено {} единиц продукции в партию batch_id={}",
                len(products),
                batch_id,
            )

        if all_products:
            # batch_detail мог быть возвращён ранее с устаревшим (без новых
            # единиц) списком products; batch_ids — все партии, задетые этим
            # вызовом (может быть несколько за один запрос). dashboard_stats
            # тоже меняется — total_products вырос.
            await invalidate_batch_caches(batch_ids, invalidate_list=False)

        return all_products

    async def aggregate_products(
        self, batch_id: int, unique_codes: list[str]
    ) -> list[Product]:
        """
        Агрегировать продукцию по уникальным кодам.

        Проверяет:
        - Партия существует
        - Партия не закрыта
        - Коды принадлежат партии
        - Продукция ещё не агрегирована
        """
        batch = await self._batch_repo.get_by_id(batch_id)
        if batch is None:
            raise NotFoundError(f"Партия с id={batch_id} не найдена")
        if batch.is_closed:
            raise ValidationError(
                f"Партия id={batch_id} закрыта — агрегация невозможна"
            )

        products = await self._product_repo.get_by_codes_and_batch(
            batch_id, unique_codes
        )

        found_codes = {p.unique_code for p in products}
        missing = set(unique_codes) - found_codes
        if missing:
            raise NotFoundError(
                f"Коды не найдены в партии: {', '.join(sorted(missing))}"
            )

        already = [p.unique_code for p in products if p.is_aggregated]
        if already:
            raise ConflictError(f"Уже агрегированы: {', '.join(sorted(already))}")

        aggregated = await self._product_repo.aggregate_products(
            [p.id for p in products]
        )
        logger.info(
            "Агрегировано {} единиц в партии batch_id={}",
            len(aggregated),
            batch_id,
        )

        webhook_service = WebhookService(self._session)
        for p in aggregated:
            await webhook_service.dispatch_event(
                "product_aggregated",
                {
                    "unique_code": p.unique_code,
                    "batch_id": p.batch_id,
                    "batch_number": batch.batch_number,
                    "aggregated_at": (
                        p.aggregated_at.isoformat() if p.aggregated_at else None
                    ),
                },
            )

        if aggregated:
            await invalidate_batch_caches(batch_id, invalidate_list=False)

        return aggregated
