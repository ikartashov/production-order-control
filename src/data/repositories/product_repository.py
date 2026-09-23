from datetime import datetime

from sqlalchemy import func, select

from data.models.batch import Batch
from data.models.product import Product
from data.models.work_center import WorkCenter
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

    async def get_global_stats(self) -> tuple[int, int]:
        """Получить (всего единиц, агрегировано) по всей продукции одним COUNT-запросом."""
        result = await self._session.execute(
            select(
                func.count(Product.id),
                func.count(Product.id).filter(Product.is_aggregated.is_(True)),
            )
        )
        total, aggregated = result.one()
        return total, aggregated

    async def get_stats_for_batches(
        self, batch_ids: list[int]
    ) -> dict[int, tuple[int, int]]:
        """Получить {batch_id: (всего единиц, агрегировано)} для набора партий.

        Один агрегатный запрос с ``GROUP BY batch_id`` вместо отдельного
        ``get_batch_stats`` на каждую партию — используется в
        ``AnalyticsService.compare_batches`` для устранения N+1. Партии без
        продукции в выдаче отсутствуют (GROUP BY не даёт строку для пустой
        группы) — вызывающий код должен подставлять ``(0, 0)`` по умолчанию.
        """
        if not batch_ids:
            return {}
        result = await self._session.execute(
            select(
                Product.batch_id,
                func.count(Product.id),
                func.count(Product.id).filter(Product.is_aggregated.is_(True)),
            )
            .where(Product.batch_id.in_(batch_ids))
            .group_by(Product.batch_id)
        )
        return {
            batch_id: (total, aggregated)
            for batch_id, total, aggregated in result.all()
        }

    async def get_today_counts(self, today_start: datetime) -> tuple[int, int]:
        """Получить (добавлено сегодня, агрегировано сегодня) одним COUNT-запросом.

        ``today_start`` — начало текущих суток по UTC, передаётся вызывающим
        кодом (та же точка отсчёта, что и в ``BatchRepository.get_today_counts``).
        """
        result = await self._session.execute(
            select(
                func.count(Product.id).filter(Product.created_at >= today_start),
                func.count(Product.id).filter(Product.aggregated_at >= today_start),
            )
        )
        added_today, aggregated_today = result.one()
        return added_today, aggregated_today

    async def get_shift_stats(self) -> dict[str, tuple[int, int]]:
        """Получить {смена: (всего продукции, агрегировано)} через JOIN на Batch.

        ``Product`` не хранит смену напрямую — она есть только на партии,
        поэтому нужен JOIN. Один агрегатный запрос с GROUP BY по
        ``Batch.shift`` вместо отдельного запроса на каждое значение смены.
        Смены без единой продукции в выдаче не появляются — вызывающий код
        (``compute_dashboard_stats``) должен подставлять ``(0, 0)`` по
        умолчанию, комбинируя с ``BatchRepository.get_shift_counts``.
        """
        result = await self._session.execute(
            select(
                Batch.shift,
                func.count(Product.id),
                func.count(Product.id).filter(Product.is_aggregated.is_(True)),
            )
            .join(Batch, Product.batch_id == Batch.id)
            .group_by(Batch.shift)
        )
        return {shift: (total, aggregated) for shift, total, aggregated in result.all()}

    async def get_work_center_stats(self) -> dict[str, tuple[int, int]]:
        """Получить {identifier рабочего центра: (всего продукции, агрегировано)}.

        JOIN ``Product`` -> ``Batch`` -> ``WorkCenter``, GROUP BY по
        рабочему центру. Считает по всем рабочим центрам сразу (не только по
        топ-5) — вызывающий код (``compute_dashboard_stats``) сам выбирает из
        результата нужные identifier'ы, полученные из
        ``BatchRepository.get_top_work_centers_by_batches``, подставляя
        ``(0, 0)`` для тех, у кого ещё нет продукции.
        """
        result = await self._session.execute(
            select(
                WorkCenter.identifier,
                func.count(Product.id),
                func.count(Product.id).filter(Product.is_aggregated.is_(True)),
            )
            .join(Batch, Product.batch_id == Batch.id)
            .join(WorkCenter, Batch.work_center_id == WorkCenter.id)
            .group_by(WorkCenter.id, WorkCenter.identifier)
        )
        return {
            identifier: (total, aggregated)
            for identifier, total, aggregated in result.all()
        }
