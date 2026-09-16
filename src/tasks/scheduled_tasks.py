"""Периодические (Celery Beat) задачи: обслуживание системы по расписанию."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from celery_app import celery_app
from core.cache import cache_delete, cache_delete_pattern, cache_set
from core.database import get_session
from data.models.batch import Batch
from data.models.product import Product
from data.models.webhook import WebhookSubscription
from data.repositories.batch_repository import BatchRepository
from data.repositories.product_repository import ProductRepository
from data.repositories.webhook_repository import WebhookRepository
from domain.services.analytics_service import compute_dashboard_stats
from domain.services.webhook_service import WebhookService
from storage.minio_service import BUCKETS, MinIOService
from tasks.webhook_tasks import send_webhook

# Файлы старше этого срока удаляются задачей cleanup_old_files.
OLD_FILES_MAX_AGE_DAYS = 30


@celery_app.task(bind=True)  # type: ignore[untyped-decorator]
def auto_close_expired_batches(self: Any) -> dict[str, Any]:
    """Автоматически закрыть партии, у которых смена уже завершилась."""
    return asyncio.run(_auto_close_expired_batches_async())


async def _auto_close_expired_batches_async() -> dict[str, Any]:
    """Закрыть все просроченные открытые партии одним UPDATE и разослать вебхуки.

    Раньше каждая партия закрывалась через полный `BatchService.update_batch`
    (repo UPDATE + cache_delete_pattern + 2×cache_delete + re-fetch SELECT на
    партию) — для дневного sweep из N просроченных партий это N
    последовательных наборов DB/Redis round trip'ов. Теперь: статистика по
    каждой партии считается до закрытия, партии закрываются одним bulk
    UPDATE, а событие `batch_closed` (с точной статистикой) всё равно
    рассылается по одному на партию — подписчики не должны заметить разницы.
    """
    async with get_session() as session:
        batch_repo = BatchRepository(Batch, session)
        product_repo = ProductRepository(Product, session)
        expired = await batch_repo.get_expired_open_batches()

        if not expired:
            logger.info("Автозакрытие партий: закрыто 0 из 0 просроченных")
            return {"success": True, "closed": 0, "batch_ids": []}

        # Статистика по каждой партии считается ДО закрытия — сам факт
        # закрытия на неё не влияет, но так порядок операций зеркалит
        # прежнее поведение BatchService.update_batch.
        stats_by_batch_id: dict[int, tuple[int, int]] = {}
        for expired_batch in expired:
            stats_by_batch_id[expired_batch.id] = await product_repo.get_batch_stats(
                expired_batch.id
            )

        closed_ids = [expired_batch.id for expired_batch in expired]
        now = datetime.now(UTC)
        await batch_repo.bulk_close(closed_ids)

        webhook_service = WebhookService(session)
        for expired_batch in expired:
            total, aggregated = stats_by_batch_id[expired_batch.id]
            rate = (aggregated / total * 100) if total else 0
            await webhook_service.dispatch_event(
                "batch_closed",
                {
                    "id": expired_batch.id,
                    "batch_number": expired_batch.batch_number,
                    "closed_at": now.isoformat(),
                    "statistics": {
                        "total_products": total,
                        "aggregated": aggregated,
                        "aggregation_rate": rate,
                    },
                },
            )

        await cache_delete_pattern("batches_list:*")
        await cache_delete("dashboard_stats")
        for batch_id in closed_ids:
            await cache_delete(f"batch_detail:{batch_id}")

        logger.info(
            "Автозакрытие партий: закрыто {} из {} просроченных",
            len(closed_ids),
            len(expired),
        )
        return {"success": True, "closed": len(closed_ids), "batch_ids": closed_ids}


@celery_app.task(bind=True)  # type: ignore[untyped-decorator]
def cleanup_old_files(self: Any) -> dict[str, Any]:
    """Удалить из MinIO файлы во всех бакетах старше 30 дней."""
    return _cleanup_old_files_sync()


def _cleanup_old_files_sync() -> dict[str, Any]:
    minio = MinIOService()
    cutoff = datetime.now(UTC) - timedelta(days=OLD_FILES_MAX_AGE_DAYS)

    deleted: list[str] = []
    for bucket in BUCKETS:
        for obj in minio.list_files(bucket):
            last_modified = obj.last_modified
            object_name = obj.object_name
            if (
                last_modified is not None
                and object_name is not None
                and last_modified < cutoff
            ):
                minio.delete_file(bucket, object_name)
                deleted.append(f"{bucket}/{object_name}")

    logger.info("Очистка старых файлов: удалено {} файлов", len(deleted))
    return {"success": True, "deleted": len(deleted), "files": deleted}


@celery_app.task(bind=True)  # type: ignore[untyped-decorator]
def update_cached_statistics(self: Any) -> dict[str, Any]:
    """Пересчитать сводную статистику для дашборда и закешировать её в Redis."""
    return asyncio.run(_update_cached_statistics_async())


async def _update_cached_statistics_async() -> dict[str, Any]:
    # Вычисление статистики делегировано общей функции
    # analytics_service.compute_dashboard_stats — единственному источнику
    # правды для набора полей дашборда. Раньше здесь был отдельный
    # хендролленный запрос, чей результат не включал closed_batches, из-за
    # чего закешированное значение расходилось с тем, что писал
    # AnalyticsService.get_dashboard_stats, и проваливало валидацию
    # DashboardSummary на чтении. Теперь оба источника пишут под ключ
    # "dashboard_stats" идентичный по составу словарь по построению.
    #
    # Остаточная гонка (принимается, см. docstring compute_dashboard_stats):
    # если между чтением статистики здесь и cache_set ниже партия/продукция
    # изменится и инвалидирует кеш, значение может на несколько секунд
    # отстать от реальности — это ожидаемый побочный эффект периодического
    # push-обновления с TTL=300с, а не баг.
    async with get_session() as session:
        stats = await compute_dashboard_stats(session)

    await cache_set("dashboard_stats", stats, ttl=300)
    logger.info("Статистика дашборда обновлена в кеше: {}", stats)
    return {"success": True, "stats": stats}


@celery_app.task(bind=True)  # type: ignore[untyped-decorator]
def retry_failed_webhooks(self: Any) -> dict[str, Any]:
    """Повторно поставить в очередь доставку неудачных вебхуков, у которых остались попытки."""
    return asyncio.run(_retry_failed_webhooks_async())


async def _retry_failed_webhooks_async() -> dict[str, Any]:
    async with get_session() as session:
        repo = WebhookRepository(WebhookSubscription, session)
        deliveries = await repo.claim_deliveries_to_retry()

        for delivery in deliveries:
            send_webhook.delay(delivery.id)

        logger.info(
            "Повторная отправка вебхуков: поставлено в очередь {} доставок",
            len(deliveries),
        )
        return {"success": True, "retried": len(deliveries)}
