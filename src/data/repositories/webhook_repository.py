from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from data.models.webhook import WebhookDelivery, WebhookSubscription
from data.repositories.base_repository import BaseRepository


class WebhookRepository(BaseRepository[WebhookSubscription]):
    """Репозиторий для работы с подписками на вебхуки и их доставками."""

    async def get_list(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[WebhookSubscription], int]:
        """Получить список подписок с пагинацией."""
        query = select(WebhookSubscription)

        # Считаем total отдельным запросом
        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            query.order_by(WebhookSubscription.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    async def update(
        self, subscription: WebhookSubscription, **kwargs: Any
    ) -> WebhookSubscription:
        """Обновить поля подписки."""
        for key, value in kwargs.items():
            setattr(subscription, key, value)
        await self._session.flush()
        return subscription

    async def get_active_by_event(self, event_type: str) -> list[WebhookSubscription]:
        """Получить активные подписки, подписанные на данный тип события."""
        result = await self._session.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.is_active.is_(True),
                WebhookSubscription.events.any(event_type),  # type: ignore[arg-type]
            )
        )
        return list(result.scalars().all())

    # ── Доставки ─────────────────────────────────────────────

    async def create_delivery(
        self,
        subscription_id: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Создать запись о доставке вебхука."""
        delivery = WebhookDelivery(
            subscription_id=subscription_id,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempts=0,
            created_at=datetime.now(UTC),
        )
        self._session.add(delivery)
        await self._session.flush()
        return delivery

    async def get_delivery_by_id(self, delivery_id: int) -> WebhookDelivery | None:
        """Получить доставку по ID вместе с подпиской."""
        result = await self._session.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id)
            .options(joinedload(WebhookDelivery.subscription))
        )
        return result.unique().scalar_one_or_none()

    async def update_delivery(
        self, delivery: WebhookDelivery, **kwargs: Any
    ) -> WebhookDelivery:
        """Обновить поля записи о доставке."""
        for key, value in kwargs.items():
            setattr(delivery, key, value)
        await self._session.flush()
        return delivery

    async def get_deliveries_list(
        self,
        subscription_id: int,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[WebhookDelivery], int]:
        """Получить список доставок подписки с пагинацией."""
        query = select(WebhookDelivery).where(
            WebhookDelivery.subscription_id == subscription_id
        )

        count_result = await self._session.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar_one()

        result = await self._session.execute(
            query.order_by(WebhookDelivery.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all()), total
