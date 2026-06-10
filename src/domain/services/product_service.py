from itertools import groupby
from operator import itemgetter
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError, ValidationError
from src.data.models.batch import Batch
from src.data.models.product import Product
from src.data.repositories.batch_repository import BatchRepository
from src.data.repositories.product_repository import ProductRepository


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

        return all_products
