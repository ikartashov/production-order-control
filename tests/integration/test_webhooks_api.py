"""Интеграционные тесты API вебхуков (webhooks): CRUD подписки, через
полный стек до реального Postgres.

Намеренно НЕ создаются партии в этих тестах: ``BatchService`` рассылает
события (``batch_created``/``batch_closed``) активным подпискам через
``WebhookService.dispatch_event`` -> Celery ``send_webhook.delay(...)``,
что требует живого брокера (RabbitMQ). Эти тесты проверяют только
CRUD подписки самой по себе (без наступления событий), поэтому брокер не
нужен — см. ``domain/services/webhook_service.py::dispatch_event``, который
ничего не делает, если подходящих активных подписок нет.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.webhook import WebhookSubscription

pytestmark = pytest.mark.asyncio


async def _create_webhook(
    client: AsyncClient, webhook_payload_factory: Any, **overrides: Any
) -> dict[str, Any]:
    payload = webhook_payload_factory(**overrides)
    response = await client.post("/api/v1/webhooks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateWebhook:
    async def test_create_webhook_returns_201_and_persists_to_db(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_payload_factory: Any,
    ) -> None:
        payload = webhook_payload_factory(url="https://example.com/hooks/create-test")

        response = await client.post("/api/v1/webhooks", json=payload)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["url"] == payload["url"]
        assert body["events"] == payload["events"]
        assert body["is_active"] is True
        assert body["retry_count"] == payload["retry_count"]

        result = await db_session.execute(
            select(WebhookSubscription).where(WebhookSubscription.id == body["id"])
        )
        db_webhook = result.scalar_one()
        assert db_webhook.url == payload["url"]
        assert db_webhook.secret_key == payload["secret_key"]

    async def test_create_webhook_rejects_unknown_fields(
        self, client: AsyncClient, webhook_payload_factory: Any
    ) -> None:
        payload = webhook_payload_factory()
        payload["unexpected_field"] = "not-allowed"

        response = await client.post("/api/v1/webhooks", json=payload)

        assert response.status_code == 422


class TestListWebhooks:
    async def test_list_webhooks_includes_created_ones(
        self, client: AsyncClient, webhook_payload_factory: Any
    ) -> None:
        first = await _create_webhook(
            client, webhook_payload_factory, url="https://example.com/hooks/list-1"
        )
        second = await _create_webhook(
            client, webhook_payload_factory, url="https://example.com/hooks/list-2"
        )

        response = await client.get("/api/v1/webhooks", params={"limit": 100})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 2
        ids = {item["id"] for item in body["items"]}
        assert first["id"] in ids
        assert second["id"] in ids


class TestUpdateWebhook:
    async def test_update_webhook_patches_fields(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_payload_factory: Any,
    ) -> None:
        created = await _create_webhook(client, webhook_payload_factory)

        response = await client.patch(
            f"/api/v1/webhooks/{created['id']}",
            json={"is_active": False, "retry_count": 5},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["is_active"] is False
        assert body["retry_count"] == 5
        # Поля, не переданные в PATCH, не должны измениться.
        assert body["url"] == created["url"]

        result = await db_session.execute(
            select(WebhookSubscription).where(WebhookSubscription.id == created["id"])
        )
        db_webhook = result.scalar_one()
        assert db_webhook.is_active is False
        assert db_webhook.retry_count == 5

    async def test_update_webhook_missing_returns_404(
        self, client: AsyncClient
    ) -> None:
        response = await client.patch(
            "/api/v1/webhooks/999999999", json={"is_active": False}
        )

        assert response.status_code == 404


class TestDeleteWebhook:
    async def test_delete_webhook_removes_it_from_db(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        webhook_payload_factory: Any,
    ) -> None:
        created = await _create_webhook(client, webhook_payload_factory)

        response = await client.delete(f"/api/v1/webhooks/{created['id']}")

        assert response.status_code == 204

        result = await db_session.execute(
            select(WebhookSubscription).where(WebhookSubscription.id == created["id"])
        )
        assert result.scalar_one_or_none() is None

        list_response = await client.get("/api/v1/webhooks", params={"limit": 100})
        ids = {item["id"] for item in list_response.json()["items"]}
        assert created["id"] not in ids

    async def test_delete_webhook_missing_returns_404(
        self, client: AsyncClient
    ) -> None:
        response = await client.delete("/api/v1/webhooks/999999999")

        assert response.status_code == 404
