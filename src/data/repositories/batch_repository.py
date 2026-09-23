from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload, selectinload

from data.models.batch import Batch
from data.models.work_center import WorkCenter
from data.repositories.base_repository import BaseRepository


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

    async def get_summary_counts(self) -> tuple[int, int]:
        """Получить (всего партий, активных партий) одним COUNT-запросом."""
        result = await self._session.execute(
            select(
                func.count(Batch.id),
                func.count(Batch.id).filter(Batch.is_closed.is_(False)),
            )
        )
        total, active = result.one()
        return total, active

    async def get_by_ids(self, batch_ids: list[int]) -> list[Batch]:
        """Получить партии по списку ID одним запросом (без связей).

        Используется для батч-сравнения (``AnalyticsService.compare_batches``),
        чтобы избежать N+1 из последовательных запросов по одной партии.
        """
        if not batch_ids:
            return []
        result = await self._session.execute(
            select(Batch).where(Batch.id.in_(batch_ids))
        )
        return list(result.scalars().all())

    async def get_expired_open_batches(self) -> list[Batch]:
        """Получить открытые партии, у которых смена уже завершилась (shift_end < now)."""
        result = await self._session.execute(
            select(Batch).where(
                Batch.is_closed.is_(False),
                Batch.shift_end < datetime.now(UTC),
            )
        )
        return list(result.scalars().all())

    async def bulk_close(self, batch_ids: list[int]) -> None:
        """Массово закрыть партии (is_closed=True, closed_at=now) одним UPDATE."""
        if not batch_ids:
            return
        now = datetime.now(UTC)
        await self._session.execute(
            update(Batch)
            .where(Batch.id.in_(batch_ids))
            .values(is_closed=True, closed_at=now)
        )
        await self._session.flush()

    async def get_today_counts(self, today_start: datetime) -> tuple[int, int]:
        """Получить (создано сегодня, закрыто сегодня) одним COUNT-запросом.

        ``today_start`` — начало текущих суток по UTC, вычисляется один раз
        вызывающим кодом (``compute_dashboard_stats``) и переиспользуется для
        всех "сегодняшних" метрик дашборда, чтобы не было рассинхронизации
        границы суток между партиями и продукцией.
        """
        result = await self._session.execute(
            select(
                func.count(Batch.id).filter(Batch.created_at >= today_start),
                func.count(Batch.id).filter(Batch.closed_at >= today_start),
            )
        )
        created_today, closed_today = result.one()
        return created_today, closed_today

    async def get_shift_counts(self) -> dict[str, int]:
        """Получить количество партий по каждому значению смены (GROUP BY shift).

        ``shift`` — свободный текст, а не enum, поэтому ключи словаря — это
        ровно те значения, что реально встречаются в данных.
        """
        result = await self._session.execute(
            select(Batch.shift, func.count(Batch.id)).group_by(Batch.shift)
        )
        counts: dict[str, int] = {}
        for shift, count in result.all():
            counts[shift] = count
        return counts

    async def get_top_work_centers_by_batches(
        self, limit: int = 5
    ) -> list[tuple[str, str, int]]:
        """Топ рабочих центров по числу партий: (identifier, name, batches_count).

        Один агрегатный запрос с JOIN на ``WorkCenter`` и GROUP BY по нему,
        отсортированный по количеству партий по убыванию и ограниченный
        ``limit``. Долю продукции/агрегации по этим рабочим центрам
        досчитывает ``ProductRepository.get_work_center_stats`` — здесь
        только партийная часть блока ``top_work_centers`` дашборда.
        """
        result = await self._session.execute(
            select(
                WorkCenter.identifier,
                WorkCenter.name,
                func.count(Batch.id),
            )
            .join(WorkCenter, Batch.work_center_id == WorkCenter.id)
            .group_by(WorkCenter.id, WorkCenter.identifier, WorkCenter.name)
            .order_by(func.count(Batch.id).desc())
            .limit(limit)
        )
        return list(result.tuples().all())
