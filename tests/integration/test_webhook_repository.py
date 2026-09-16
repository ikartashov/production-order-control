"""Интеграционные тесты ``WebhookRepository`` (реальный Postgres).

Фокус — на атомарном "захвате" неудачных доставок для повтора
(``claim_deliveries_to_retry``), регрессионный тест на дублирующую
отправку вебхуков при параллельных sweep'ах ``retry_failed_webhooks``
(см. ``~/.claude/rules/celery-webhooks.md`` про идемпотентность при
at-least-once доставке).
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.webhook import WebhookDelivery, WebhookSubscription
from data.repositories.webhook_repository import WebhookRepository

pytestmark = pytest.mark.asyncio


async def _make_subscription(
    session: AsyncSession, *, retry_count: int = 3
) -> WebhookSubscription:
    subscription = WebhookSubscription(
        url="https://example.com/hooks/retry-test",
        events=["batch_created"],
        secret_key="test-secret-key-0123456789",
        is_active=True,
        retry_count=retry_count,
        timeout=10,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def _make_delivery(
    session: AsyncSession,
    subscription: WebhookSubscription,
    *,
    status: str = "failed",
    attempts: int = 1,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        subscription_id=subscription.id,
        event_type="batch_created",
        payload={"event": "batch_created", "data": {}},
        status=status,
        attempts=attempts,
        created_at=datetime.now(UTC),
    )
    session.add(delivery)
    await session.flush()
    return delivery


class TestClaimDeliveriesToRetry:
    """Регрессионные тесты на атомарность захвата доставок для повтора."""

    async def test_claims_failed_delivery_and_transitions_status(
        self, db_session: AsyncSession
    ) -> None:
        """Захваченная доставка немедленно переходит из ``failed`` в
        ``retrying`` — до того, как вызывающий код поставит
        ``send_webhook.delay(...)`` в очередь."""
        subscription = await _make_subscription(db_session)
        delivery = await _make_delivery(db_session, subscription, status="failed")

        repo = WebhookRepository(WebhookSubscription, db_session)
        claimed = await repo.claim_deliveries_to_retry()

        assert [d.id for d in claimed] == [delivery.id]
        # Статус уже сменился на "retrying" в той же транзакции/round-trip,
        # раньше чем вызывающий код успеет поставить задачу в очередь.
        assert claimed[0].status == "retrying"

    async def test_second_call_does_not_reclaim_same_delivery(
        self, db_session: AsyncSession
    ) -> None:
        """Повторный sweep сразу после первого больше не видит ту же
        доставку — она уже не в статусе ``failed``, поэтому второй
        независимый ``send_webhook.delay()`` для неё не будет поставлен."""
        subscription = await _make_subscription(db_session)
        await _make_delivery(db_session, subscription, status="failed")

        repo = WebhookRepository(WebhookSubscription, db_session)
        first_claim = await repo.claim_deliveries_to_retry()
        second_claim = await repo.claim_deliveries_to_retry()

        assert len(first_claim) == 1
        assert second_claim == []

    async def test_exhausted_retries_are_not_claimed(
        self, db_session: AsyncSession
    ) -> None:
        subscription = await _make_subscription(db_session, retry_count=3)
        await _make_delivery(db_session, subscription, status="failed", attempts=3)

        repo = WebhookRepository(WebhookSubscription, db_session)
        claimed = await repo.claim_deliveries_to_retry()

        assert claimed == []

    async def test_non_failed_deliveries_are_not_claimed(
        self, db_session: AsyncSession
    ) -> None:
        subscription = await _make_subscription(db_session)
        await _make_delivery(db_session, subscription, status="success", attempts=1)
        await _make_delivery(db_session, subscription, status="pending", attempts=0)
        await _make_delivery(db_session, subscription, status="retrying", attempts=1)

        repo = WebhookRepository(WebhookSubscription, db_session)
        claimed = await repo.claim_deliveries_to_retry()

        assert claimed == []
