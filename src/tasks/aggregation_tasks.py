"""Celery-задача массовой (частично-успешной) агрегации продукции."""

import asyncio
from typing import Any

from loguru import logger

from celery_app import celery_app
from core.database import get_session
from core.exceptions import ConflictError, NotFoundError, ValidationError
from domain.services.product_service import ProductService


@celery_app.task(bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def aggregate_products_batch(
    self: Any,
    batch_id: int,
    unique_codes: list[str],
    user_id: int | None = None,
) -> dict[str, Any]:
    """Агрегировать список уникальных кодов в партии, code-за-code.

    В отличие от ``ProductService.aggregate_products`` (all-or-nothing),
    эта задача обрабатывает каждый код отдельно и возвращает частичный
    результат: сколько агрегировано успешно, сколько с ошибкой и почему.
    """
    return asyncio.run(_aggregate_products_batch_async(self, batch_id, unique_codes))


async def _aggregate_products_batch_async(
    task: Any, batch_id: int, unique_codes: list[str]
) -> dict[str, Any]:
    total = len(unique_codes)
    aggregated_count = 0
    errors: list[dict[str, str]] = []

    async with get_session() as session:
        service = ProductService(session)

        for index, code in enumerate(unique_codes, start=1):
            try:
                await service.aggregate_products(batch_id, [code])
                aggregated_count += 1
            except (NotFoundError, ConflictError, ValidationError) as exc:
                errors.append({"code": code, "reason": exc.detail})
                logger.warning(
                    "Ошибка агрегации кода {} в партии batch_id={}: {}",
                    code,
                    batch_id,
                    exc.detail,
                )

            task.update_state(
                state="PROGRESS",
                meta={
                    "current": index,
                    "total": total,
                    "progress": round(index / total * 100, 2) if total else 100.0,
                },
            )

    logger.info(
        "Асинхронная агрегация завершена batch_id={} total={} aggregated={} failed={}",
        batch_id,
        total,
        aggregated_count,
        len(errors),
    )

    return {
        "success": True,
        "total": total,
        "aggregated": aggregated_count,
        "failed": len(errors),
        "errors": errors,
    }
