from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.cache import cache_get, cache_set
from core.exceptions import NotFoundError
from data.models.batch import Batch
from data.models.product import Product
from data.repositories.batch_repository import BatchRepository
from data.repositories.product_repository import ProductRepository
from domain.services.batch_service import BatchService

DASHBOARD_CACHE_KEY = "dashboard_stats"
DASHBOARD_CACHE_TTL = 300


async def compute_dashboard_stats(session: AsyncSession) -> dict[str, Any]:
    """Вычислить сводную статистику дашборда напрямую из репозиториев.

    Единственный источник правды для набора полей дашборда
    (``total_batches``, ``active_batches``, ``closed_batches``,
    ``total_products``, ``aggregated_products``, ``aggregation_rate``,
    ``cached_at``). Используется и из ``AnalyticsService.get_dashboard_stats``
    (cache-aside при промахе кеша по запросу), и из периодической задачи
    Celery Beat ``update_cached_statistics`` (push раз в 5 минут) —
    обе пишут результат под один и тот же ключ Redis ``dashboard_stats``,
    поэтому набор полей теперь совпадает по построению, а не по
    договорённости между двумя независимыми реализациями (ранее
    ``scheduled_tasks`` дублировал этот запрос вручную и не включал
    ``closed_batches``, из-за чего закешированное значение проваливало
    валидацию ``DashboardSummary`` на чтении).

    Функция не трогает кеш — это чистое вычисление, кеширование остаётся
    на стороне вызывающего кода.

    Остаточная гонка (принимается, не устраняется): если запись/удаление
    партии или продукции инвалидирует ключ ``dashboard_stats`` (через
    ``cache_delete``) между чтением статистики здесь и последующим
    ``cache_set`` у вызывающей стороны (например, в push-задаче Beat), кеш
    может ненадолго вернуться к снимку, отстающему на несколько секунд. Это
    неизбежный побочный эффект схемы "периодический push + pull-through по
    запросу" с TTL=300с, а не баг — для этого use case не стоит вводить
    распределённую блокировку или счётчик версий ради устранения такого
    узкого окна.
    """
    batch_repo = BatchRepository(Batch, session)
    product_repo = ProductRepository(Product, session)

    total_batches, active_batches = await batch_repo.get_summary_counts()
    total_products, aggregated_products = await product_repo.get_global_stats()
    aggregation_rate = (
        aggregated_products / total_products * 100 if total_products else 0.0
    )

    return {
        "total_batches": total_batches,
        "active_batches": active_batches,
        "closed_batches": total_batches - active_batches,
        "total_products": total_products,
        "aggregated_products": aggregated_products,
        "aggregation_rate": aggregation_rate,
        "cached_at": datetime.now(UTC).isoformat(),
    }


class AnalyticsService:
    """Сервис аналитики: дашборд, статистика по партии, сравнение партий."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._batch_repo = BatchRepository(Batch, session)
        self._product_repo = ProductRepository(Product, session)
        # Переиспользуем BatchService.get_batch для проверки существования партии
        # (единая логика 404 для всех эндпоинтов, работающих с конкретной партией).
        self._batch_service = BatchService(session)

    async def get_dashboard_stats(self) -> dict[str, Any]:
        """Сводная статистика для дашборда (cache-aside, TTL=300с).

        При попадании в кеш возвращает закешированное значение как есть.
        При промахе — вычисляет статистику через общую функцию
        ``compute_dashboard_stats`` и кеширует результат под тем же ключом
        ``dashboard_stats``, которым пользуется периодическая задача Celery
        Beat ``update_cached_statistics`` (она тоже вызывает
        ``compute_dashboard_stats``). Набор полей одинаков по построению —
        оба источника взаимозаменяемы с точки зрения клиента.
        """
        cached = await cache_get(DASHBOARD_CACHE_KEY)
        if cached is not None:
            return dict(cached)

        result = await compute_dashboard_stats(self._session)
        await cache_set(DASHBOARD_CACHE_KEY, result, ttl=DASHBOARD_CACHE_TTL)
        return result

    async def get_batch_statistics(self, batch_id: int) -> dict[str, Any]:
        """Статистика и таймлайн выполнения по одной партии.

        ``timeline`` считается от ``shift_start``/``shift_end`` партии и
        текущего момента (``datetime.now(UTC)``):

        - ``shift_duration_hours`` — полная длительность смены;
        - ``elapsed_hours`` — сколько часов смены уже прошло, зажато в
          диапазоне ``[0, shift_duration_hours]`` (смена ещё не началась —
          0; смена уже закончилась — вся её длительность);
        - ``products_per_hour`` — текущая скорость агрегации
          (``aggregated / elapsed_hours``), 0 пока ``elapsed_hours == 0``;
        - ``estimated_completion`` — простая линейная проекция: если
          агрегировать больше нечего — момент окончания смены; иначе, при
          ненулевой текущей скорости — ``now + remaining / products_per_hour``;
          иначе (скорость ещё неизвестна) — ``None``. Это оценочная
          проекция по текущему темпу, а не гарантия.
        """
        batch = await self._batch_service.get_batch(batch_id)
        total, aggregated = await self._product_repo.get_batch_stats(batch_id)
        remaining = total - aggregated
        rate = (aggregated / total * 100) if total else 0.0

        now = datetime.now(UTC)
        shift_duration_hours = (
            batch.shift_end - batch.shift_start
        ).total_seconds() / 3600
        elapsed_hours = max(
            0.0, (min(now, batch.shift_end) - batch.shift_start).total_seconds() / 3600
        )
        elapsed_hours = min(elapsed_hours, shift_duration_hours)
        products_per_hour = aggregated / elapsed_hours if elapsed_hours > 0 else 0.0

        estimated_completion: str | None
        if remaining <= 0:
            estimated_completion = batch.shift_end.isoformat()
        elif products_per_hour > 0:
            hours_needed = remaining / products_per_hour
            estimated_completion = (now + timedelta(hours=hours_needed)).isoformat()
        else:
            estimated_completion = None

        return {
            "batch_info": {
                "id": batch.id,
                "batch_number": batch.batch_number,
                "batch_date": batch.batch_date,
                "is_closed": batch.is_closed,
            },
            "production_stats": {
                "total_products": total,
                "aggregated": aggregated,
                "remaining": remaining,
                "aggregation_rate": rate,
            },
            "timeline": {
                "shift_duration_hours": shift_duration_hours,
                "elapsed_hours": elapsed_hours,
                "products_per_hour": products_per_hour,
                "estimated_completion": estimated_completion,
            },
        }

    async def compare_batches(self, batch_ids: list[int]) -> dict[str, Any]:
        """Сравнить несколько партий по продуктивности.

        Для отсутствующего ``batch_id`` поднимается ``NotFoundError`` (через
        ``BatchService.get_batch``) — намеренный выбор в пользу явной ошибки,
        а не тихого пропуска: ошибка в списке ID от клиента не должна
        приводить к молчаливо неполному сравнению.

        ``products_per_hour`` здесь считается от ``shift_duration_hours``
        (длительности всей смены), а не от "прошедшего" времени, как в
        ``get_batch_statistics`` — сравниваемые партии как правило уже
        завершены, и такой знаменатель даёт стабильную, воспроизводимую
        "почасовую производительность за смену" для сопоставления партий
        между собой.
        """
        if not batch_ids:
            return {
                "comparison": [],
                "average": {"aggregation_rate": 0.0, "products_per_hour": 0.0},
            }

        # Один запрос за всеми партиями + один агрегатный запрос за
        # статистикой продукции по всем партиям сразу — вместо 2N
        # последовательных round trip'ов (get_batch + get_batch_stats на
        # каждый batch_id).
        batches = await self._batch_repo.get_by_ids(batch_ids)
        batches_by_id = {batch.id: batch for batch in batches}
        missing_id = next(
            (batch_id for batch_id in batch_ids if batch_id not in batches_by_id),
            None,
        )
        if missing_id is not None:
            raise NotFoundError(f"Партия с id={missing_id} не найдена")

        stats_by_batch = await self._product_repo.get_stats_for_batches(batch_ids)

        comparison: list[dict[str, Any]] = []
        for batch_id in batch_ids:
            batch = batches_by_id[batch_id]
            total, aggregated = stats_by_batch.get(batch_id, (0, 0))
            rate = (aggregated / total * 100) if total else 0.0
            duration_hours = (
                batch.shift_end - batch.shift_start
            ).total_seconds() / 3600
            products_per_hour = (
                aggregated / duration_hours if duration_hours > 0 else 0.0
            )

            comparison.append(
                {
                    "batch_id": batch.id,
                    "batch_number": batch.batch_number,
                    "total_products": total,
                    "aggregated": aggregated,
                    "rate": rate,
                    "duration_hours": duration_hours,
                    "products_per_hour": products_per_hour,
                }
            )

        avg_rate = sum(item["rate"] for item in comparison) / len(comparison)
        avg_pph = sum(item["products_per_hour"] for item in comparison) / len(
            comparison
        )

        return {
            "comparison": comparison,
            "average": {
                "aggregation_rate": avg_rate,
                "products_per_hour": avg_pph,
            },
        }
