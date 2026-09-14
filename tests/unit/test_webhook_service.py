import hashlib
import hmac
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from core.exceptions import NotFoundError
from data.models.webhook import WebhookDelivery, WebhookSubscription
from domain.services.webhook_service import WebhookService, compute_signature

WEBHOOK_REPO_PATH = "domain.services.webhook_service.WebhookRepository"
SEND_WEBHOOK_PATH = "tasks.webhook_tasks.send_webhook"


@contextmanager
def patch_webhook_service_repos(mock_webhook_repo: AsyncMock) -> Iterator[None]:
    """Патчит репозиторий WebhookService."""
    with patch(WEBHOOK_REPO_PATH, return_value=mock_webhook_repo):
        yield


def make_subscription(
    id_: int = 1,
    url: str = "https://example.com/hook",
    events: list[str] | None = None,
    secret_key: str = "s" * 32,
    is_active: bool = True,
    retry_count: int = 3,
    timeout: int = 10,
) -> WebhookSubscription:
    """Фабрика тестовой подписки на вебхук."""
    sub = WebhookSubscription()
    sub.id = id_
    sub.url = url
    sub.events = events if events is not None else ["batch_created"]
    sub.secret_key = secret_key
    sub.is_active = is_active
    sub.retry_count = retry_count
    sub.timeout = timeout
    sub.created_at = datetime(2024, 1, 30, 8, 0, 0)
    sub.updated_at = datetime(2024, 1, 30, 8, 0, 0)
    return sub


def make_delivery(
    id_: int = 1,
    subscription_id: int = 1,
    event_type: str = "batch_created",
    payload: dict | None = None,
) -> WebhookDelivery:
    """Фабрика тестовой доставки вебхука."""
    delivery = WebhookDelivery()
    delivery.id = id_
    delivery.subscription_id = subscription_id
    delivery.event_type = event_type
    delivery.payload = payload or {"event": event_type, "data": {}, "timestamp": "t"}
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.response_status = None
    delivery.response_body = None
    delivery.error_message = None
    delivery.created_at = datetime(2024, 1, 30, 8, 0, 0)
    delivery.delivered_at = None
    return delivery


# Тесты create_subscription


class TestCreateSubscription:
    async def test_creates_subscription(self, mock_session: AsyncMock) -> None:
        """Подписка создаётся без ошибок."""
        subscription = make_subscription()
        mock_repo = AsyncMock()
        mock_repo.create.return_value = subscription

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            result = await service.create_subscription(
                {"url": "https://example.com/hook", "events": ["batch_created"]}
            )

        assert result.id == 1
        mock_repo.create.assert_awaited_once()


# Тесты get_subscription


class TestGetSubscription:
    async def test_returns_subscription_by_id(self, mock_session: AsyncMock) -> None:
        """Существующая подписка возвращается корректно."""
        subscription = make_subscription()
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = subscription

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            result = await service.get_subscription(1)

        assert result.id == 1
        mock_repo.get_by_id.assert_awaited_once_with(1)

    async def test_missing_id_raises_not_found(self, mock_session: AsyncMock) -> None:
        """NotFoundError, если подписка не найдена."""
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = None

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            with pytest.raises(NotFoundError):
                await service.get_subscription(999)


# Тесты list_subscriptions


class TestListSubscriptions:
    async def test_returns_items_and_total(self, mock_session: AsyncMock) -> None:
        """Список подписок и общее количество возвращаются корректно."""
        subscription = make_subscription()
        mock_repo = AsyncMock()
        mock_repo.get_list.return_value = ([subscription], 1)

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            subs, total = await service.list_subscriptions()

        assert total == 1
        assert len(subs) == 1


# Тесты update_subscription


class TestUpdateSubscription:
    async def test_updates_subscription(self, mock_session: AsyncMock) -> None:
        """Подписка обновляется корректно."""
        subscription = make_subscription()
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = subscription
        mock_repo.update.return_value = subscription

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            await service.update_subscription(1, {"is_active": False})

        mock_repo.update.assert_awaited_once_with(subscription, is_active=False)

    async def test_missing_id_raises_not_found(self, mock_session: AsyncMock) -> None:
        """NotFoundError при обновлении несуществующей подписки."""
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = None

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            with pytest.raises(NotFoundError):
                await service.update_subscription(999, {"is_active": False})


# Тесты delete_subscription


class TestDeleteSubscription:
    async def test_deletes_subscription(self, mock_session: AsyncMock) -> None:
        """Подписка удаляется без ошибок."""
        subscription = make_subscription()
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = subscription

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            await service.delete_subscription(1)

        mock_repo.delete.assert_awaited_once_with(subscription)

    async def test_missing_id_raises_not_found(self, mock_session: AsyncMock) -> None:
        """NotFoundError при удалении несуществующей подписки."""
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = None

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            with pytest.raises(NotFoundError):
                await service.delete_subscription(999)


# Тесты list_deliveries


class TestListDeliveries:
    async def test_returns_items_and_total(self, mock_session: AsyncMock) -> None:
        """Список доставок и общее количество возвращаются корректно."""
        subscription = make_subscription()
        delivery = make_delivery()
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = subscription
        mock_repo.get_deliveries_list.return_value = ([delivery], 1)

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            deliveries, total = await service.list_deliveries(1)

        assert total == 1
        assert len(deliveries) == 1
        mock_repo.get_deliveries_list.assert_awaited_once_with(1, offset=0, limit=20)

    async def test_missing_subscription_raises_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        """NotFoundError, если подписка не существует."""
        mock_repo = AsyncMock()
        mock_repo.get_by_id.return_value = None

        with patch_webhook_service_repos(mock_repo):
            service = WebhookService(mock_session)
            with pytest.raises(NotFoundError):
                await service.list_deliveries(999)


# Тесты HMAC-подписи


class TestComputeSignature:
    def test_signature_matches_independent_computation(self) -> None:
        """Подпись совпадает с независимо вычисленной эталонной подписью."""
        secret = "my-very-secret-key-1234567890ab"
        payload = {
            "event": "batch_created",
            "data": {"id": 1, "batch_number": 22222},
            "timestamp": "2024-01-30T08:00:00+00:00",
        }

        expected_body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        expected = hmac.new(
            secret.encode("utf-8"), expected_body, hashlib.sha256
        ).hexdigest()

        assert compute_signature(secret, payload) == expected

    def test_different_secrets_produce_different_signatures(self) -> None:
        """Разные секреты дают разные подписи для одного и того же payload."""
        payload = {"event": "x", "data": {}, "timestamp": "t"}
        sig1 = compute_signature("secret-one", payload)
        sig2 = compute_signature("secret-two", payload)
        assert sig1 != sig2

    def test_signature_is_deterministic(self) -> None:
        """Одинаковый payload и секрет всегда дают одинаковую подпись."""
        payload = {"event": "x", "data": {"a": 1, "b": 2}, "timestamp": "t"}
        assert compute_signature("secret", payload) == compute_signature(
            "secret", payload
        )


# Тесты dispatch_event


class TestDispatchEvent:
    async def test_notifies_only_matching_active_subscriptions(
        self, mock_session: AsyncMock
    ) -> None:
        """dispatch_event запрашивает только активные подписки на нужное событие."""
        subscription = make_subscription(events=["batch_created"])
        delivery = make_delivery(subscription_id=subscription.id)

        mock_repo = AsyncMock()
        mock_repo.get_active_by_event.return_value = [subscription]
        mock_repo.create_delivery.return_value = delivery

        with (
            patch_webhook_service_repos(mock_repo),
            patch(f"{SEND_WEBHOOK_PATH}.delay") as mock_delay,
        ):
            service = WebhookService(mock_session)
            await service.dispatch_event("batch_created", {"id": 1})

        mock_repo.get_active_by_event.assert_awaited_once_with("batch_created")
        mock_delay.assert_called_once_with(delivery.id)

    async def test_no_matching_subscriptions_creates_no_deliveries(
        self, mock_session: AsyncMock
    ) -> None:
        """Если нет подходящих подписок — доставки не создаются и delay не вызывается."""
        mock_repo = AsyncMock()
        mock_repo.get_active_by_event.return_value = []

        with (
            patch_webhook_service_repos(mock_repo),
            patch(f"{SEND_WEBHOOK_PATH}.delay") as mock_delay,
        ):
            service = WebhookService(mock_session)
            await service.dispatch_event("batch_created", {"id": 1})

        mock_repo.create_delivery.assert_not_awaited()
        mock_delay.assert_not_called()

    async def test_creates_one_delivery_per_matching_subscription(
        self, mock_session: AsyncMock
    ) -> None:
        """Для каждой подходящей подписки создаётся ровно одна доставка."""
        sub1 = make_subscription(id_=1, events=["batch_created"])
        sub2 = make_subscription(id_=2, events=["batch_created"])
        delivery1 = make_delivery(id_=1, subscription_id=1)
        delivery2 = make_delivery(id_=2, subscription_id=2)

        mock_repo = AsyncMock()
        mock_repo.get_active_by_event.return_value = [sub1, sub2]
        mock_repo.create_delivery.side_effect = [delivery1, delivery2]

        with (
            patch_webhook_service_repos(mock_repo),
            patch(f"{SEND_WEBHOOK_PATH}.delay") as mock_delay,
        ):
            service = WebhookService(mock_session)
            await service.dispatch_event("batch_created", {"id": 1})

        assert mock_repo.create_delivery.await_count == 2
        assert mock_delay.call_count == 2
        mock_delay.assert_any_call(delivery1.id)
        mock_delay.assert_any_call(delivery2.id)

    async def test_payload_shape_includes_event_data_timestamp(
        self, mock_session: AsyncMock
    ) -> None:
        """payload, переданный в create_delivery, содержит event/data/timestamp."""
        subscription = make_subscription(events=["product_aggregated"])
        delivery = make_delivery(subscription_id=subscription.id)

        mock_repo = AsyncMock()
        mock_repo.get_active_by_event.return_value = [subscription]
        mock_repo.create_delivery.return_value = delivery

        with (
            patch_webhook_service_repos(mock_repo),
            patch(f"{SEND_WEBHOOK_PATH}.delay"),
        ):
            service = WebhookService(mock_session)
            await service.dispatch_event(
                "product_aggregated", {"unique_code": "CODE001"}
            )

        _, kwargs = mock_repo.create_delivery.call_args
        payload = kwargs["payload"]
        assert payload["event"] == "product_aggregated"
        assert payload["data"] == {"unique_code": "CODE001"}
        assert "timestamp" in payload
        # Проверяем, что timestamp — валидная ISO8601 строка (UTC)
        datetime.fromisoformat(payload["timestamp"])

    async def test_inactive_subscription_not_returned_by_repo_is_skipped(
        self, mock_session: AsyncMock
    ) -> None:
        """Неактивные/несовпадающие подписки не фильтруются в сервисе —
        это обязанность репозитория (get_active_by_event); сервис просто
        не создаёт доставок, если репозиторий вернул пустой список."""
        mock_repo = AsyncMock()
        mock_repo.get_active_by_event.return_value = []

        with (
            patch_webhook_service_repos(mock_repo),
            patch(f"{SEND_WEBHOOK_PATH}.delay") as mock_delay,
        ):
            service = WebhookService(mock_session)
            await service.dispatch_event("batch_updated", {"id": 1})

        mock_delay.assert_not_called()
