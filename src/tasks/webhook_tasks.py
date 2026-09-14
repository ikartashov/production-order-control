"""Celery-задача асинхронной доставки вебхуков подписчикам."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

from celery_app import celery_app
from core.database import get_session
from data.models.webhook import WebhookSubscription
from data.repositories.webhook_repository import WebhookRepository
from domain.services.webhook_service import compute_signature

# Сколько раз повторить попытку прочитать WebhookDelivery, если строка ещё не
# закоммичена в БД (гонка между HTTP-запросом и воркером Celery).
DELIVERY_LOOKUP_MAX_RETRIES = 2
DELIVERY_LOOKUP_COUNTDOWN = 2

RESPONSE_BODY_MAX_LENGTH = 2000


@celery_app.task(bind=True, max_retries=3)  # type: ignore[untyped-decorator]
def send_webhook(self: Any, delivery_id: int) -> dict[str, Any]:
    """Отправить одно webhook-уведомление подписчику.

    Синхронная обёртка Celery-задачи над асинхронной логикой (доступ к БД
    и HTTP-запрос выполняются в ``_send_webhook``).
    """
    return asyncio.run(_send_webhook(self, delivery_id))


async def _send_webhook(task: Any, delivery_id: int) -> dict[str, Any]:
    """Асинхронная логика доставки: читает delivery, подписывает и шлёт POST."""
    async with get_session() as session:
        repo = WebhookRepository(WebhookSubscription, session)
        delivery = await repo.get_delivery_by_id(delivery_id)

        if delivery is None:
            if task.request.retries < DELIVERY_LOOKUP_MAX_RETRIES:
                logger.warning(
                    "WebhookDelivery id={} ещё не найдена (возможна гонка "
                    "с коммитом transaction) — повтор через {}с",
                    delivery_id,
                    DELIVERY_LOOKUP_COUNTDOWN,
                )
                task.retry(countdown=DELIVERY_LOOKUP_COUNTDOWN)
                return {"status": "retry"}
            logger.error(
                "WebhookDelivery id={} не найдена после {} повторов",
                delivery_id,
                DELIVERY_LOOKUP_MAX_RETRIES,
            )
            return {"status": "not_found"}

        subscription = delivery.subscription
        signature = compute_signature(subscription.secret_key, delivery.payload)

        try:
            with httpx.Client(timeout=subscription.timeout) as client:
                response = client.post(
                    subscription.url,
                    json=delivery.payload,
                    headers={"X-Webhook-Signature": signature},
                )
        except httpx.HTTPError as exc:
            await repo.update_delivery(
                delivery,
                attempts=delivery.attempts + 1,
                status="failed",
                error_message=str(exc),
            )
            logger.warning(
                "Ошибка отправки вебхука delivery_id={}: {}", delivery_id, exc
            )
            return _maybe_retry(task, delivery_id)

        attempts = delivery.attempts + 1
        response_body = response.text[:RESPONSE_BODY_MAX_LENGTH]

        if 200 <= response.status_code < 300:
            await repo.update_delivery(
                delivery,
                attempts=attempts,
                status="success",
                response_status=response.status_code,
                response_body=response_body,
                delivered_at=datetime.now(UTC),
            )
            logger.info("Вебхук delivery_id={} доставлен успешно", delivery_id)
            return {"status": "success"}

        await repo.update_delivery(
            delivery,
            attempts=attempts,
            status="failed",
            response_status=response.status_code,
            response_body=response_body,
            error_message=f"HTTP {response.status_code}",
        )
        logger.warning(
            "Вебхук delivery_id={} получил статус {}",
            delivery_id,
            response.status_code,
        )
        return _maybe_retry(task, delivery_id)


def _maybe_retry(task: Any, delivery_id: int) -> dict[str, Any]:
    """Повторить задачу с экспоненциальной задержкой, пока не исчерпаны попытки."""
    if task.request.retries < task.max_retries:
        task.retry(countdown=2**task.request.retries)
        return {"status": "retry"}
    logger.error(
        "Вебхук delivery_id={} не доставлен после исчерпания попыток", delivery_id
    )
    return {"status": "failed"}
