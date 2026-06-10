from sqlalchemy import select

from src.data.models.product import Product
from src.data.repositories.base_repository import BaseRepository


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
