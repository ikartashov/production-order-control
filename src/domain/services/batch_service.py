from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from data.models.batch import Batch
from data.models.work_center import WorkCenter
from data.repositories.batch_repository import BatchRepository
from data.repositories.work_center_repository import WorkCenterRepository


class BatchService:
    """Сервис управления партиями."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._batch_repo = BatchRepository(Batch, session)
        self._wc_repo = WorkCenterRepository(WorkCenter, session)

    async def create_batches(self, items: list[dict[str, Any]]) -> list[Batch]:
        """
        Атомарно создать список партий.

        Проверяет дубли по batch_number + batch_date.
        Автоматически создаёт WorkCenter при необходимости.
        """
        # Проверяем дубли внутри самого запроса
        seen: set[tuple[int, str]] = set()
        for item in items:
            key = (item["batch_number"], str(item["batch_date"]))
            if key in seen:
                raise ConflictError(
                    f"Дублирующая партия в запросе: номер {item['batch_number']} "
                    f"дата {item['batch_date']}"
                )
            seen.add(key)

        # Проверяем дубли в БД
        for item in items:
            existing = await self._batch_repo.get_by_number_and_date(
                item["batch_number"], item["batch_date"]
            )
            if existing:
                raise ConflictError(
                    f"Партия с номером {item['batch_number']} "
                    f"и датой {item['batch_date']} уже существует"
                )

        batches: list[Batch] = []
        for item in items:
            wc = await self._wc_repo.get_or_create(
                identifier=item["wc_identifier"],
                name=item["wc_name"],
            )

            batch = await self._batch_repo.create(
                is_closed=item["is_closed"],
                task_description=item["task_description"],
                work_center_id=wc.id,
                shift=item["shift"],
                team=item["team"],
                batch_number=item["batch_number"],
                batch_date=item["batch_date"],
                nomenclature=item["nomenclature"],
                ekn_code=item["ekn_code"],
                shift_start=item["shift_start"],
                shift_end=item["shift_end"],
            )
            batches.append(batch)
            logger.info(
                "Создана партия batch_id={} batch_number={}",
                batch.id,
                batch.batch_number,
            )

        return batches

    async def get_batch(self, batch_id: int) -> Batch:
        """Получить партию со связями или 404."""
        batch = await self._batch_repo.get_by_id_with_relations(batch_id)
        if batch is None:
            raise NotFoundError(f"Партия с id={batch_id} не найдена")
        return batch

    async def update_batch(self, batch_id: int, data: dict[str, Any]) -> Batch:
        """
        Обновить партию.

        При закрытии устанавливает closed_at = now().
        При открытии сбрасывает closed_at = None.
        """
        batch = await self._batch_repo.get_by_id_with_relations(batch_id)
        if batch is None:
            raise NotFoundError(f"Партия с id={batch_id} не найдена")

        update_data = {k: v for k, v in data.items() if v is not None}

        if "is_closed" in update_data:
            if update_data["is_closed"] and not batch.is_closed:
                update_data["closed_at"] = datetime.now(UTC)
            elif not update_data["is_closed"] and batch.is_closed:
                update_data["closed_at"] = None

        await self._batch_repo.update(batch, **update_data)
        logger.info("Обновлена партия batch_id={}", batch_id)
        return await self.get_batch(batch_id)

    async def get_batches_list(
        self, filters: dict[str, Any]
    ) -> tuple[list[Batch], int]:
        """Получить список партий с фильтрацией."""
        return await self._batch_repo.get_list(**filters)
