import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.v1.schemas.batch import BatchListItem, BatchResponse, WorkCenterResponse
from api.v1.schemas.batch import ProductResponse as BatchProductResponse
from core.cache import cache_delete, cache_delete_pattern, cache_get, cache_set
from core.exceptions import ConflictError, NotFoundError
from data.models.batch import Batch
from data.models.product import Product
from data.models.work_center import WorkCenter
from data.repositories.batch_repository import BatchRepository
from data.repositories.product_repository import ProductRepository
from data.repositories.work_center_repository import WorkCenterRepository
from domain.services.webhook_service import WebhookService

# TODO(analytics): "dashboard_stats" / "batch_statistics:{id}" — read paths for
# these belong to the future analytics endpoints (owned by another agent).
# Once those endpoints exist, wrap their service methods with
# @cached(ttl=..., key_prefix="dashboard_stats"/"batch_statistics"). This
# module only *invalidates* those keys on write, which is safe/forward
# compatible even though nothing populates them yet.


# --- Сериализация Batch (+ связи) в JSON-совместимый dict и обратно ---
#
# `get_batch`/`get_batches_list` ниже кешируют результат вручную (без
# @cached), потому что они возвращают SQLAlchemy ORM-объекты (Batch, в т.ч.
# со связями work_center/products), а `cache_set`/`cache_get` в core/cache.py
# сериализуют значения через json.dumps(value, default=str)/json.loads —
# это НЕ умеет корректно провести ORM-объект через кеш и восстановить его
# связи. Вместо того чтобы вручную перечислять список полей дважды (в
# to-dict и в from-dict, как раньше), сериализация/десериализация
# переиспользует уже существующие Pydantic-схемы ответа API
# (BatchResponse/BatchListItem, `from_attributes=True`) — они и так
# описывают ровно тот набор полей, что нужен вызывающему коду:
#   * to-dict = ``Schema.model_validate(batch).model_dump(mode="json")`` —
#     схема сама знает нужные поля и валидирует их;
#   * from-dict = ``Schema.model_validate(data)`` возвращает типизированный
#     Pydantic-объект (даты/datetime уже распарсены из ISO-строк самим
#     Pydantic), из которого мы собираем транзиентный (не привязанный к
#     сессии) объект Batch простым присваиванием атрибутов — ровно так же,
#     как это делают фикстуры в tests/conftest.py. Единственное, что нельзя
#     сделать автоматически — это связи (work_center/products): Pydantic
#     возвращает для них вложенные модели (WorkCenterResponse/
#     ProductResponse), а роутерам и BatchResponse/BatchListItem нужны
#     настоящие ORM-объекты WorkCenter/Product с атрибутным доступом,
#     поэтому для них есть два маленьких вспомогательных конструктора ниже.


def _work_center_from_response(wc: WorkCenterResponse) -> WorkCenter:
    work_center = WorkCenter()
    work_center.id = wc.id
    work_center.identifier = wc.identifier
    work_center.name = wc.name
    return work_center


def _product_from_response(p: BatchProductResponse) -> Product:
    product = Product()
    product.id = p.id
    product.unique_code = p.unique_code
    product.is_aggregated = p.is_aggregated
    product.aggregated_at = p.aggregated_at
    return product


def _batch_detail_to_dict(batch: Batch) -> dict[str, Any]:
    """Полное представление партии (для get_batch): со связями work_center и products."""
    return BatchResponse.model_validate(batch).model_dump(mode="json")


def _batch_detail_from_dict(data: dict[str, Any]) -> Batch:
    validated = BatchResponse.model_validate(data)
    batch = Batch()
    for field, value in validated:
        if field == "work_center":
            batch.work_center = _work_center_from_response(value)
        elif field == "products":
            batch.products = [_product_from_response(p) for p in value]
        else:
            setattr(batch, field, value)
    return batch


def _batch_list_item_to_dict(batch: Batch) -> dict[str, Any]:
    """Облегчённое представление партии для списка (get_batches_list).

    Без products — BatchRepository.get_list их не подгружает (см. репозиторий),
    а BatchListItem их и не использует.
    """
    return BatchListItem.model_validate(batch).model_dump(mode="json")


def _batch_list_item_from_dict(data: dict[str, Any]) -> Batch:
    validated = BatchListItem.model_validate(data)
    batch = Batch()
    for field, value in validated:
        if field == "work_center":
            batch.work_center = _work_center_from_response(value)
        else:
            setattr(batch, field, value)
    return batch


async def invalidate_batch_caches(
    batch_id: int | Iterable[int] | None = None, *, invalidate_list: bool = True
) -> None:
    """Инвалидировать все производные Redis-кеши, затронутые записью в Batch/Product.

    Единая точка инвалидации для всех мест, где меняются партии/продукция —
    чтобы при добавлении нового кеш-ключа в будущем не пришлось вспоминать
    все места записи по отдельности.

    - ``invalidate_list=True`` сбрасывает ``batches_list:*`` — нужен, когда
      изменились поля, попадающие в BatchListItem (например, сама партия
      создана/обновлена). Для изменений внутри products (добавление/
      агрегация) список партий не читает products, поэтому вызывающая
      сторона передаёт ``invalidate_list=False``.
    - ``batch_id`` — один id, несколько (например, add_products может задеть
      сразу несколько партий за один вызов) или ``None`` (например,
      create_batches — для только что созданных партий detail/statistics
      кеш ещё не существует, инвалидировать нечего).
    - ``dashboard_stats`` инвалидируется всегда — он агрегирует данные по
      всем партиям/продукции, так что любой из этих кейсов может его задеть.
    """
    if invalidate_list:
        await cache_delete_pattern("batches_list:*")
    if batch_id is not None:
        ids = [batch_id] if isinstance(batch_id, int) else batch_id
        for bid in ids:
            await cache_delete(f"batch_detail:{bid}")
            await cache_delete(f"batch_statistics:{bid}")
    await cache_delete("dashboard_stats")


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

        if batches:
            await invalidate_batch_caches(invalidate_list=True)

        return batches

    async def get_batch(self, batch_id: int) -> Batch:
        """Получить партию со связями или 404.

        Кешируется вручную (не через @cached) на 600с — см. комментарий
        над сериализаторами `_batch_detail_to_dict`/`_batch_detail_from_dict`
        в начале модуля для объяснения, почему декоратор здесь не подходит.
        """
        cache_key = f"batch_detail:{batch_id}"
        cached_data = await cache_get(cache_key)
        if cached_data is not None:
            return _batch_detail_from_dict(cached_data)

        batch = await self._batch_repo.get_by_id_with_relations(batch_id)
        if batch is None:
            raise NotFoundError(f"Партия с id={batch_id} не найдена")

        await cache_set(cache_key, _batch_detail_to_dict(batch), 600)
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

        await invalidate_batch_caches(batch_id, invalidate_list=True)

        return await self.get_batch(batch_id)

    async def get_batches_list(
        self, filters: dict[str, Any]
    ) -> tuple[list[Batch], int]:
        """Получить список партий с фильтрацией.

        Кешируется вручную (не через @cached) на 60с — по тем же причинам,
        что и `get_batch` (см. комментарий над сериализаторами в начале
        модуля). Ключ кеша строится из сериализованных фильтров.
        """
        cache_key = "batches_list:" + json.dumps(filters, sort_keys=True, default=str)
        cached_data = await cache_get(cache_key)
        if cached_data is not None:
            items = [_batch_list_item_from_dict(d) for d in cached_data["items"]]
            return items, int(cached_data["total"])

        batches, total = await self._batch_repo.get_list(**filters)
        await cache_set(
            cache_key,
            {
                "items": [_batch_list_item_to_dict(b) for b in batches],
                "total": total,
            },
            60,
        )
        return batches, total
