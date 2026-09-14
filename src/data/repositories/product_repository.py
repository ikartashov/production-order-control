from sqlalchemy import func, select

from data.models.product import Product
from data.repositories.base_repository import BaseRepository


class ProductRepository(BaseRepository[Product]):
    """Репозиторий для работы с продукцией."""

    async def get_by_unique_code(self, unique_code: str) -> Product | None:
        """Найти продукцию по уникальному коду."""
        result = await self._session.execute(
            select(Product).where(Product.unique_code == unique_code)
        )
        return result.scalar_one_or_none()

    async def get_existing_codes(self, unique_codes: list[str]) -> set[str]:
        """Получить множество уже существующих кодов из переданного списка."""
        result = await self._session.execute(
            select(Product.unique_code).where(Product.unique_code.in_(unique_codes))
        )
        return set(result.scalars().all())

    async def bulk_create(
        self, batch_id: int, unique_codes: list[str]
    ) -> list[Product]:
        """Массово создать продукцию для партии."""
        products = [
            Product(unique_code=code, batch_id=batch_id) for code in unique_codes
        ]
        self._session.add_all(products)
        await self._session.flush()
        return products

    async def get_by_codes_and_batch(
        self, batch_id: int, unique_codes: list[str]
    ) -> list[Product]:
        """Получить продукцию по кодам в рамках партии."""
        result = await self._session.execute(
            select(Product).where(
                Product.batch_id == batch_id,
                Product.unique_code.in_(unique_codes),
            )
        )
        return list(result.scalars().all())

    async def aggregate_products(self, product_ids: list[int]) -> list[Product]:
        """Установить флаг агрегации для списка продуктов."""
        from datetime import UTC, datetime

        result = await self._session.execute(
            select(Product).where(Product.id.in_(product_ids))
        )
        products = list(result.scalars().all())
        now = datetime.now(UTC)
        for product in products:
            product.is_aggregated = True
            product.aggregated_at = now
        await self._session.flush()
        return products

    async def get_batch_stats(self, batch_id: int) -> tuple[int, int]:
        """Получить (всего единиц, агрегировано) для партии одним COUNT-запросом."""
        result = await self._session.execute(
            select(
                func.count(Product.id),
                func.count(Product.id).filter(Product.is_aggregated.is_(True)),
            ).where(Product.batch_id == batch_id)
        )
        total, aggregated = result.one()
        return total, aggregated
