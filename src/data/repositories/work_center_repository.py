from sqlalchemy import func, select

from data.models.work_center import WorkCenter
from data.repositories.base_repository import BaseRepository


class WorkCenterRepository(BaseRepository[WorkCenter]):
    """Репозиторий для работы с рабочими центрами."""

    async def get_by_identifier(self, identifier: str) -> WorkCenter | None:
        """Найти РЦ по идентификатору."""
        result = await self._session.execute(
            select(WorkCenter).where(WorkCenter.identifier == identifier)
        )
        return result.scalar_one_or_none()

    async def get_list(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[WorkCenter], int]:
        """Получить список рабочих центров с пагинацией."""
        query = select(WorkCenter)

        # Считаем total отдельным запросом
        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            query.order_by(WorkCenter.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def get_or_create(self, identifier: str, name: str) -> WorkCenter:
        """Получить существующий или создать новый РЦ."""
        wc = await self.get_by_identifier(identifier)
        if wc is None:
            wc = await self.create(identifier=identifier, name=name)
        return wc
