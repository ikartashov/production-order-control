from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from data.models.batch import Batch
from data.models.product import Product
from data.models.work_center import WorkCenter
from data.repositories.batch_repository import BatchRepository
from data.repositories.product_repository import ProductRepository
from data.repositories.work_center_repository import WorkCenterRepository
from domain.services.webhook_service import WebhookService


class BatchService:
    """Сервис управления партиями."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._batch_repo = BatchRepository(Batch, session)
        self._wc_repo = WorkCenterRepository(WorkCenter, session)
        self._product_repo = ProductRepository(Product, session)

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
        webhook_service = WebhookService(self._session)
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

            await webhook_service.dispatch_event(
                "batch_created",
                {
                    "id": batch.id,
                    "batch_number": batch.batch_number,
                    "batch_date": batch.batch_date.isoformat(),
                    "nomenclature": batch.nomenclature,
                    "work_center": wc.name,
                },
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

        was_closed = batch.is_closed
        became_closed = False
        if "is_closed" in update_data:
            if update_data["is_closed"] and not batch.is_closed:
                update_data["closed_at"] = datetime.now(UTC)
                became_closed = True
            elif not update_data["is_closed"] and batch.is_closed:
                update_data["closed_at"] = None

        await self._batch_repo.update(batch, **update_data)
        logger.info("Обновлена партия batch_id={}", batch_id)

        webhook_service = WebhookService(self._session)
        if became_closed and not was_closed:
            total, aggregated = await self._product_repo.get_batch_stats(batch_id)
            rate = (aggregated / total * 100) if total else 0
            await webhook_service.dispatch_event(
                "batch_closed",
                {
                    "id": batch.id,
                    "batch_number": batch.batch_number,
                    "closed_at": (
                        batch.closed_at.isoformat() if batch.closed_at else None
                    ),
                    "statistics": {
                        "total_products": total,
                        "aggregated": aggregated,
                        "aggregation_rate": rate,
                    },
                },
            )
        else:
            changes = {k: v for k, v in update_data.items() if k != "closed_at"}
            if changes:
                await webhook_service.dispatch_event(
                    "batch_updated",
                    {
                        "id": batch.id,
                        "batch_number": batch.batch_number,
                        "changes": changes,
                    },
                )

        return await self.get_batch(batch_id)

    async def get_batches_list(
        self, filters: dict[str, Any]
    ) -> tuple[list[Batch], int]:
        """Получить список партий с фильтрацией."""
        return await self._batch_repo.get_list(**filters)
