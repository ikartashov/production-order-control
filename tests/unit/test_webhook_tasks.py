from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from data.models.webhook import WebhookDelivery, WebhookSubscription
from tasks.webhook_tasks import (
    DELIVERY_LOOKUP_COUNTDOWN,
    DELIVERY_LOOKUP_MAX_RETRIES,
    _send_webhook,
)

WEBHOOK_REPO_PATH = "tasks.webhook_tasks.WebhookRepository"
GET_SESSION_PATH = "tasks.webhook_tasks.get_session"
HTTPX_CLIENT_PATH = "tasks.webhook_tasks.httpx.Client"


def make_subscription(
    secret_key: str = "s" * 32,
    url: str = "https://example.com/hook",
    timeout: int = 10,
) -> WebhookSubscription:
    sub = WebhookSubscription()
    sub.id = 1
    sub.url = url
    sub.events = ["batch_created"]
    sub.secret_key = secret_key
    sub.is_active = True
    sub.retry_count = 3
    sub.timeout = timeout
    return sub


def make_delivery(subscription: WebhookSubscription | None) -> WebhookDelivery:
    delivery = WebhookDelivery()
    delivery.id = 1
    delivery.subscription_id = 1
    delivery.event_type = "batch_created"
    delivery.payload = {"event": "batch_created", "data": {"id": 1}, "timestamp": "t"}
    delivery.status = "pending"
    delivery.attempts = 0
    delivery.response_status = None
    delivery.response_body = None
    delivery.error_message = None
    delivery.created_at = datetime(2024, 1, 30, 8, 0, 0)
    delivery.delivered_at = None
    delivery.subscription = subscription
    return delivery


def make_mock_task(retries: int = 0, max_retries: int = 3) -> MagicMock:
    """Мок для bind=True Celery-задачи с управляемым состоянием повторов."""
    task = MagicMock()
    task.request.retries = retries
    task.max_retries = max_retries
    task.retry = MagicMock()
    return task


@asynccontextmanager
async def _session_cm(session: Any):
    yield session


def _patch_session(mock_session: AsyncMock, mock_repo: AsyncMock):
    """Патчит get_session() и WebhookRepository для _send_webhook."""
    return (
        patch(GET_SESSION_PATH, side_effect=lambda: _session_cm(mock_session)),
        patch(WEBHOOK_REPO_PATH, return_value=mock_repo),
    )


def _mock_httpx_client(response: MagicMock | None = None, side_effect: Any = None):
    """Строит мок httpx.Client(...) как контекстный менеджер."""
    mock_client_cls = MagicMock()
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client.post.side_effect = side_effect
    else:
        mock_client.post.return_value = response
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client_cls.return_value.__exit__.return_value = False
    return mock_client_cls


class TestSendWebhookDeliveryNotFound:
    """Гонка: WebhookDelivery ещё не закоммичена, когда воркер её читает."""

    async def test_retries_when_delivery_not_found(
        self, mock_session: AsyncMock
    ) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = None
        task = make_mock_task(retries=0)

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2:
            result = await _send_webhook(task, 999)

        task.retry.assert_called_once_with(countdown=DELIVERY_LOOKUP_COUNTDOWN)
        assert result == {"status": "retry"}

    async def test_returns_not_found_after_exhausting_lookup_retries(
        self, mock_session: AsyncMock
    ) -> None:
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = None
        task = make_mock_task(retries=DELIVERY_LOOKUP_MAX_RETRIES)

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2:
            result = await _send_webhook(task, 999)

        task.retry.assert_not_called()
        assert result == {"status": "not_found"}


class TestSendWebhookSuccess:
    async def test_marks_delivery_success_on_2xx(self, mock_session: AsyncMock) -> None:
        subscription = make_subscription()
        delivery = make_delivery(subscription)
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = delivery
        task = make_mock_task()

        response = MagicMock(status_code=200, text="OK")
        mock_client_cls = _mock_httpx_client(response=response)

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2, patch(HTTPX_CLIENT_PATH, mock_client_cls):
            result = await _send_webhook(task, 1)

        assert result == {"status": "success"}
        _, kwargs = mock_repo.update_delivery.call_args
        assert kwargs["status"] == "success"
        assert kwargs["attempts"] == 1
        assert kwargs["response_status"] == 200
        assert kwargs["delivered_at"] is not None
        task.retry.assert_not_called()

    async def test_sends_hmac_signature_header(self, mock_session: AsyncMock) -> None:
        """Заголовок X-Webhook-Signature присутствует и совпадает с ожидаемым."""
        from domain.services.webhook_service import compute_signature

        subscription = make_subscription()
        delivery = make_delivery(subscription)
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = delivery
        task = make_mock_task()

        response = MagicMock(status_code=200, text="OK")
        mock_client_cls = _mock_httpx_client(response=response)

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2, patch(HTTPX_CLIENT_PATH, mock_client_cls):
            await _send_webhook(task, 1)

        mock_client = mock_client_cls.return_value.__enter__.return_value
        _, call_kwargs = mock_client.post.call_args
        expected_signature = compute_signature(
            subscription.secret_key, delivery.payload
        )
        assert call_kwargs["headers"]["X-Webhook-Signature"] == expected_signature


class TestSendWebhookHttpFailure:
    async def test_non_2xx_marks_failed_and_retries(
        self, mock_session: AsyncMock
    ) -> None:
        subscription = make_subscription()
        delivery = make_delivery(subscription)
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = delivery
        task = make_mock_task(retries=0, max_retries=3)

        response = MagicMock(status_code=500, text="Internal Server Error")
        mock_client_cls = _mock_httpx_client(response=response)

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2, patch(HTTPX_CLIENT_PATH, mock_client_cls):
            result = await _send_webhook(task, 1)

        _, kwargs = mock_repo.update_delivery.call_args
        assert kwargs["status"] == "failed"
        assert kwargs["response_status"] == 500
        task.retry.assert_called_once_with(countdown=1)  # 2**0
        assert result == {"status": "retry"}

    async def test_exception_marks_failed_and_retries(
        self, mock_session: AsyncMock
    ) -> None:
        subscription = make_subscription()
        delivery = make_delivery(subscription)
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = delivery
        task = make_mock_task(retries=1, max_retries=3)

        mock_client_cls = _mock_httpx_client(
            side_effect=httpx.ConnectError("connection failed")
        )

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2, patch(HTTPX_CLIENT_PATH, mock_client_cls):
            result = await _send_webhook(task, 1)

        _, kwargs = mock_repo.update_delivery.call_args
        assert kwargs["status"] == "failed"
        assert "connection failed" in kwargs["error_message"]
        task.retry.assert_called_once_with(countdown=2)  # 2**1
        assert result == {"status": "retry"}

    async def test_exhausted_retries_returns_failed_without_retry(
        self, mock_session: AsyncMock
    ) -> None:
        subscription = make_subscription()
        delivery = make_delivery(subscription)
        mock_repo = AsyncMock()
        mock_repo.get_delivery_by_id.return_value = delivery
        task = make_mock_task(retries=3, max_retries=3)

        response = MagicMock(status_code=500, text="Internal Server Error")
        mock_client_cls = _mock_httpx_client(response=response)

        p1, p2 = _patch_session(mock_session, mock_repo)
        with p1, p2, patch(HTTPX_CLIENT_PATH, mock_client_cls):
            result = await _send_webhook(task, 1)

        task.retry.assert_not_called()
        assert result == {"status": "failed"}
