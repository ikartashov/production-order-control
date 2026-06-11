from sqlalchemy import select

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

    async def get_or_create(self, identifier: str, name: str) -> WorkCenter:
        """Получить существующий или создать новый РЦ."""
        wc = await self.get_by_identifier(identifier)
        if wc is None:
            wc = await self.create(identifier=identifier, name=name)
        return wc
