from datetime import datetime

from pydantic import BaseModel


class ProductCreateItem(BaseModel):
    """Схема одной единицы продукции."""

    unique_code: str
    batch_id: int

    model_config = {"extra": "forbid"}


class ProductResponse(BaseModel):
    """Ответ по продукции."""

    id: int
    unique_code: str
    batch_id: int
    is_aggregated: bool
    aggregated_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    """Ответ списка продукции."""

    items: list[ProductResponse]
    total: int
