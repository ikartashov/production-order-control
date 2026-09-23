from datetime import datetime

from pydantic import BaseModel


class WorkCenterResponse(BaseModel):
    """Полный ответ по рабочему центру."""

    id: int
    identifier: str
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkCenterListResponse(BaseModel):
    """Ответ списка рабочих центров с пагинацией."""

    items: list[WorkCenterResponse]
    total: int
    offset: int
    limit: int
