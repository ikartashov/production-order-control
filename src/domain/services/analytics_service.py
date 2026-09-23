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

# Верхняя граница efficiency_score в team_performance (см. докстринг
# get_batch_statistics) — не пускаем в ответ абсурдные значения, когда
# смена только началась и мгновенный темп кратно превышает средний
# требуемый темп на всю смену.
EFFICIENCY_SCORE_CAP = 999.9


async def compute_dashboard_stats(session: AsyncSession) -> dict[str, Any]:
    """Вычислить сводную статистику дашборда напрямую из репозиториев.

    Единственный источник правды для набора полей дашборда
    (``total_batches``, ``active_batches``, ``closed_batches``,
    ``total_products``, ``aggregated_products``, ``aggregation_rate``,
    ``cached_at``, ``today``, ``by_shift``, ``top_work_centers``).
    Используется и из ``AnalyticsService.get_dashboard_stats``
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

    Дополнительные блоки:

    - ``today`` — счётчики за текущие сутки (``batches_created``,
      ``batches_closed``, ``products_added``, ``products_aggregated``),
      где "сегодня" — единая точка отсчёта ``today_start`` (начало текущих
      суток по UTC), посчитанная один раз и переданная в оба репозитория,
      чтобы партии и продукция не разъезжались по границе суток на доли
      секунды между двумя независимыми вызовами ``datetime.now(UTC)``.
    - ``by_shift`` — партии/продукция/агрегация в разрезе значения
      ``Batch.shift`` (свободный текст, не enum — ключи словаря это ровно
      те значения смены, что реально есть в данных). Партийная часть и
      продуктовая часть считаются двумя раздельными агрегатными запросами
      (``BatchRepository.get_shift_counts`` и
      ``ProductRepository.get_shift_stats``) и сводятся здесь по ключу
      смены — так проще, чем один сложный JOIN, и каждый запрос остаётся
      одноцелевым.
    - ``top_work_centers`` — топ-5 рабочих центров по числу партий.
      Аналогично ``by_shift``: партийный рейтинг и лимит "топ-5" считает
      ``BatchRepository.get_top_work_centers_by_batches`` (сортировка и
      ``LIMIT`` на стороне БД), а продукцию по всем рабочим центрам —
      ``ProductRepository.get_work_center_stats``; здесь они сводятся по
      ``identifier`` рабочего центра (бизнес-идентификатор из внешней
      системы, а не числовой PK).
    """
    batch_repo = BatchRepository(Batch, session)
    product_repo = ProductRepository(Product, session)

    total_batches, active_batches = await batch_repo.get_summary_counts()
    total_products, aggregated_products = await product_repo.get_global_stats()
    aggregation_rate = (
        aggregated_products / total_products * 100 if total_products else 0.0
    )

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    batches_created_today, batches_closed_today = await batch_repo.get_today_counts(
        today_start
    )
    products_added_today, products_aggregated_today = (
        await product_repo.get_today_counts(today_start)
    )

    shift_batch_counts = await batch_repo.get_shift_counts()
    shift_product_stats = await product_repo.get_shift_stats()
    by_shift = {
        shift: {
            "batches": batches_count,
            "products": shift_product_stats.get(shift, (0, 0))[0],
            "aggregated": shift_product_stats.get(shift, (0, 0))[1],
        }
        for shift, batches_count in shift_batch_counts.items()
    }

    top_work_centers_by_batches = await batch_repo.get_top_work_centers_by_batches(
        limit=5
    )
    work_center_product_stats = await product_repo.get_work_center_stats()
    top_work_centers = []
    for identifier, name, batches_count in top_work_centers_by_batches:
        wc_total, wc_aggregated = work_center_product_stats.get(identifier, (0, 0))
        wc_rate = (wc_aggregated / wc_total * 100) if wc_total else 0.0
        top_work_centers.append(
            {
                "id": identifier,
                "name": name,
                "batches_count": batches_count,
                "products_count": wc_total,
                "aggregation_rate": wc_rate,
            }
        )

    return {
        "total_batches": total_batches,
        "active_batches": active_batches,
        "closed_batches": total_batches - active_batches,
        "total_products": total_products,
        "aggregated_products": aggregated_products,
        "aggregation_rate": aggregation_rate,
        "cached_at": datetime.now(UTC).isoformat(),
        "today": {
            "batches_created": batches_created_today,
            "batches_closed": batches_closed_today,
            "products_added": products_added_today,
            "products_aggregated": products_aggregated_today,
        },
        "by_shift": by_shift,
        "top_work_centers": top_work_centers,
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

        ``team_performance`` — показатели бригады (``batch.team``), уже
        известной на момент вызова:

        - ``avg_products_per_hour`` — то же значение, что и
          ``timeline.products_per_hour`` (пересчитывать незачем, это один и
          тот же темп, просто продублированный под своим именем в блоке
          команды).
        - ``efficiency_score`` — эвристическая оценка темпа бригады как
          процент от темпа, необходимого, чтобы успеть закрыть партию ровно
          к концу смены: ``required_products_per_hour = total /
          shift_duration_hours`` (0, если длительность смены нулевая), а
          ``efficiency_score = products_per_hour / required_products_per_hour
          * 100`` (0.0, если требуемый темп нулевой — делить не на что). Это
          прокси-метрика, а не точный показатель: в БД нет отдельного
          планового количества продукции на смену, с которым можно было бы
          сравнить факт напрямую, поэтому "весь объём партии, поделённый на
          всю смену" — лучшее доступное приближение к плану. Именно поэтому
          значение зажимается сверху константой
          ``EFFICIENCY_SCORE_CAP`` (999.9): в начале смены
          ``elapsed_hours`` мало, а ``products_per_hour`` в числителе
          считается от прошедшего времени (см. выше) — на первых минутах
          смены темп может на короткое время в разы превышать средний
          требуемый темп за всю смену, и без ограничения сверху это дало бы
          абсурдно большие проценты вместо содержательной оценки.
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

        required_products_per_hour = (
            total / shift_duration_hours if shift_duration_hours > 0 else 0.0
        )
        efficiency_score = (
            min(
                products_per_hour / required_products_per_hour * 100,
                EFFICIENCY_SCORE_CAP,
            )
            if required_products_per_hour > 0
            else 0.0
        )

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
            "team_performance": {
                "team": batch.team,
                "avg_products_per_hour": products_per_hour,
                "efficiency_score": efficiency_score,
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
