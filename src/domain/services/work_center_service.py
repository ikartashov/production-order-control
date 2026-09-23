from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotFoundError
from data.models.work_center import WorkCenter
from data.repositories.work_center_repository import WorkCenterRepository


class WorkCenterService:
    """Сервис управления рабочими центрами (только чтение)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._wc_repo = WorkCenterRepository(WorkCenter, session)

    async def get_work_center(self, work_center_id: int) -> WorkCenter:
        """Получить рабочий центр по ID или 404."""
        wc = await self._wc_repo.get_by_id(work_center_id)
        if wc is None:
            raise NotFoundError(f"Рабочий центр с id={work_center_id} не найден")
        return wc

    async def get_work_centers_list(
        self, offset: int = 0, limit: int = 20
    ) -> tuple[list[WorkCenter], int]:
        """Получить список рабочих центров с пагинацией."""
        return await self._wc_repo.get_list(offset=offset, limit=limit)
