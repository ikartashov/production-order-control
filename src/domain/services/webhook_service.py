import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import NotFoundError
from data.models.webhook import WebhookDelivery, WebhookSubscription
from data.repositories.webhook_repository import WebhookRepository


def compute_signature(secret_key: str, payload: dict[str, Any]) -> str:
    """Вычислить HMAC-SHA256 подпись полезной нагрузки вебхука.

    Сериализация детерминирована (``sort_keys=True``), а ``default=str``
    подстраховывает от несериализуемых типов (например, datetime),
    если они попадут в payload.
    """
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hmac.new(secret_key.encode("utf-8"), body, hashlib.sha256).hexdigest()


class WebhookService:
    """Сервис управления подписками на вебхуки и рассылкой событий."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._webhook_repo = WebhookRepository(WebhookSubscription, session)

    # ── Подписки ─────────────────────────────────────────────

    async def create_subscription(self, data: dict[str, Any]) -> WebhookSubscription:
        """Создать подписку на вебхук."""
        subscription = await self._webhook_repo.create(**data)
        logger.info(
            "Создана подписка на вебхук id={} url={}", subscription.id, subscription.url
        )
        return subscription

    async def get_subscription(self, subscription_id: int) -> WebhookSubscription:
        """Получить подписку по ID или 404."""
        subscription = await self._webhook_repo.get_by_id(subscription_id)
        if subscription is None:
            raise NotFoundError(f"Подписка с id={subscription_id} не найдена")
        return subscription

    async def list_subscriptions(
        self, offset: int = 0, limit: int = 20
    ) -> tuple[list[WebhookSubscription], int]:
        """Получить список подписок с пагинацией."""
        return await self._webhook_repo.get_list(offset=offset, limit=limit)

    async def update_subscription(
        self, subscription_id: int, data: dict[str, Any]
    ) -> WebhookSubscription:
        """Частично обновить подписку."""
        subscription = await self._webhook_repo.get_by_id(subscription_id)
        if subscription is None:
            raise NotFoundError(f"Подписка с id={subscription_id} не найдена")
        subscription = await self._webhook_repo.update(subscription, **data)
        logger.info("Обновлена подписка на вебхук id={}", subscription_id)
        return subscription

    async def delete_subscription(self, subscription_id: int) -> None:
        """Удалить подписку (каскадно удаляет её доставки)."""
        subscription = await self._webhook_repo.get_by_id(subscription_id)
        if subscription is None:
            raise NotFoundError(f"Подписка с id={subscription_id} не найдена")
        await self._webhook_repo.delete(subscription)
        logger.info("Удалена подписка на вебхук id={}", subscription_id)

    # ── Доставки ─────────────────────────────────────────────

    async def list_deliveries(
        self, subscription_id: int, offset: int = 0, limit: int = 20
    ) -> tuple[list[WebhookDelivery], int]:
        """Получить список доставок подписки с пагинацией."""
        subscription = await self._webhook_repo.get_by_id(subscription_id)
        if subscription is None:
            raise NotFoundError(f"Подписка с id={subscription_id} не найдена")
        return await self._webhook_repo.get_deliveries_list(
            subscription_id, offset=offset, limit=limit
        )

    # ── Диспетчеризация событий ──────────────────────────────

    async def dispatch_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Разослать событие всем активным подпискам, интересующимся ``event_type``.

        Для каждой подходящей подписки создаётся запись ``WebhookDelivery`` и
        ставится асинхронная задача Celery на фактическую отправку.
        Локальный импорт задачи разрывает цикл импортов
        ``webhook_service`` <-> ``webhook_tasks`` (последний импортирует
        ``compute_signature`` из этого модуля).
        """
        from tasks.webhook_tasks import send_webhook

        subscriptions = await self._webhook_repo.get_active_by_event(event_type)
        if not subscriptions:
            return

        payload = {
            "event": event_type,
            "data": data,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        for subscription in subscriptions:
            delivery = await self._webhook_repo.create_delivery(
                subscription_id=subscription.id,
                event_type=event_type,
                payload=payload,
            )
            send_webhook.delay(delivery.id)
            logger.info(
                "Событие {} поставлено в очередь для подписки id={} (delivery_id={})",
                event_type,
                subscription.id,
                delivery.id,
            )
