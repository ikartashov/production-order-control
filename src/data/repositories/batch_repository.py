from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from src.data.models.batch import Batch
from src.data.repositories.base_repository import BaseRepository


class BatchRepository(BaseRepository[Batch]):
    """Репозиторий для работы с партиями."""

    async def get_by_id_with_relations(self, batch_id: int) -> Batch | None:
        """Получить партию со всеми связями."""
        result = await self._session.execute(
            select(Batch)
            .where(Batch.id == batch_id)
            .options(
                joinedload(Batch.work_center),
                selectinload(Batch.products),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_list(
        self,
        *,
        is_closed: bool | None = None,
        batch_number: int | None = None,
        batch_date: date | None = None,
        work_center_id: int | None = None,
        shift: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Batch], int]:
        """Получить список партий с фильтрацией и пагинацией."""
        query = select(Batch).options(joinedload(Batch.work_center))

        if is_closed is not None:
            query = query.where(Batch.is_closed == is_closed)
        if batch_number is not None:
            query = query.where(Batch.batch_number == batch_number)
        if batch_date is not None:
            query = query.where(Batch.batch_date == batch_date)
        if work_center_id is not None:
            query = query.where(Batch.work_center_id == work_center_id)
        if shift is not None:
            query = query.where(Batch.shift == shift)

        # Считаем total отдельным запросом
        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            query.order_by(Batch.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.unique().scalars().all()), total

    async def get_by_number_and_date(
        self, batch_number: int, batch_date: date
    ) -> Batch | None:
        """Найти партию по номеру и дате (уникальный составной ключ)."""
        result = await self._session.execute(
            select(Batch).where(
                Batch.batch_number == batch_number,
                Batch.batch_date == batch_date,
            )
        )
        return result.scalar_one_or_none()

    async def update(self, batch: Batch, **kwargs: object) -> Batch:
        """Обновить поля партии."""
        for key, value in kwargs.items():
            setattr(batch, key, value)
        await self._session.flush()
        return batch
