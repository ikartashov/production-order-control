from fastapi import APIRouter, Query, status

from api.v1.schemas.webhook import (
    WebhookCreateRequest,
    WebhookDeliveryListResponse,
    WebhookDeliveryResponse,
    WebhookListItem,
    WebhookListResponse,
    WebhookResponse,
    WebhookUpdateRequest,
)
from core.dependencies import DbSession
from data.models.webhook import WebhookSubscription
from domain.services.webhook_service import WebhookService

router = APIRouter(prefix="/webhooks", tags=["Вебхуки"])


def _get_service(session: DbSession) -> WebhookService:
    return WebhookService(session)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WebhookResponse)
async def create_webhook(
    payload: WebhookCreateRequest,
    session: DbSession,
) -> WebhookSubscription:
    """Создать подписку на вебхук."""
    service = _get_service(session)
    return await service.create_subscription(payload.model_dump())


@router.get("", response_model=WebhookListResponse)
async def list_webhooks(
    session: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> WebhookListResponse:
    """Список подписок на вебхуки с пагинацией."""
    service = _get_service(session)
    subscriptions, total = await service.list_subscriptions(offset=offset, limit=limit)
    return WebhookListResponse(
        items=[WebhookListItem.model_validate(s) for s in subscriptions],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: int,
    payload: WebhookUpdateRequest,
    session: DbSession,
) -> WebhookSubscription:
    """Частично обновить подписку на вебхук."""
    service = _get_service(session)
    return await service.update_subscription(
        webhook_id, payload.model_dump(exclude_none=True)
    )


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: int, session: DbSession) -> None:
    """Удалить подписку на вебхук (каскадно удаляет её доставки)."""
    service = _get_service(session)
    await service.delete_subscription(webhook_id)


@router.get("/{webhook_id}/deliveries", response_model=WebhookDeliveryListResponse)
async def list_webhook_deliveries(
    webhook_id: int,
    session: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> WebhookDeliveryListResponse:
    """Список попыток доставки для подписки."""
    service = _get_service(session)
    deliveries, total = await service.list_deliveries(
        webhook_id, offset=offset, limit=limit
    )
    return WebhookDeliveryListResponse(
        items=[WebhookDeliveryResponse.model_validate(d) for d in deliveries],
        total=total,
        offset=offset,
        limit=limit,
    )
