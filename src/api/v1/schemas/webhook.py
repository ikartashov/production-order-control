from datetime import datetime

from pydantic import BaseModel, Field


class WebhookCreateRequest(BaseModel):
    """Схема создания подписки на вебхук."""

    url: str
    events: list[str]
    secret_key: str
    retry_count: int = 3
    timeout: int = 10

    model_config = {"extra": "forbid"}


class WebhookUpdateRequest(BaseModel):
    """Схема частичного обновления подписки на вебхук (PATCH)."""

    url: str | None = None
    events: list[str] | None = None
    secret_key: str | None = None
    is_active: bool | None = None
    retry_count: int | None = None
    timeout: int | None = None

    model_config = {"extra": "forbid"}


class WebhookResponse(BaseModel):
    """Полный ответ по подписке на вебхук."""

    id: int
    url: str
    events: list[str]
    is_active: bool
    retry_count: int
    timeout: int
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookListItem(BaseModel):
    """Элемент списка подписок на вебхуки."""

    id: int
    url: str
    events: list[str]
    is_active: bool

    model_config = {"from_attributes": True}


class WebhookListResponse(BaseModel):
    """Ответ списка подписок с пагинацией."""

    items: list[WebhookListItem]
    total: int
    offset: int = Field(default=0)
    limit: int = Field(default=20)


class WebhookDeliveryResponse(BaseModel):
    """Ответ по одной попытке доставки вебхука."""

    id: int
    event_type: str
    status: str
    attempts: int
    response_status: int | None
    error_message: str | None
    created_at: datetime
    delivered_at: datetime | None

    model_config = {"from_attributes": True}


class WebhookDeliveryListResponse(BaseModel):
    """Ответ списка доставок с пагинацией."""

    items: list[WebhookDeliveryResponse]
    total: int
    offset: int = Field(default=0)
    limit: int = Field(default=20)
