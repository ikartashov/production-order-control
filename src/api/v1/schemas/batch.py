from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class BatchCreateItem(BaseModel):
    """Схема одного задания из запроса 1С."""

    # Поля приходят с кириллическими именами из 1С
    СтатусЗакрытия: bool = Field(alias="СтатусЗакрытия", default=False)
    ПредставлениеЗаданияНаСмену: str = Field(alias="ПредставлениеЗаданияНаСмену")
    РабочийЦентр: str = Field(alias="РабочийЦентр")
    Смена: str = Field(alias="Смена")
    Бригада: str = Field(alias="Бригада")
    НомерПартии: int = Field(alias="НомерПартии")
    ДатаПартии: date = Field(alias="ДатаПартии")
    Номенклатура: str = Field(alias="Номенклатура")
    КодЕКН: str = Field(alias="КодЕКН")
    ИдентификаторРЦ: str = Field(alias="ИдентификаторРЦ")
    ДатаВремяНачалаСмены: datetime = Field(alias="ДатаВремяНачалаСмены")
    ДатаВремяОкончанияСмены: datetime = Field(alias="ДатаВремяОкончанияСмены")

    model_config = {"populate_by_name": True}

    @field_validator("ДатаВремяОкончанияСмены")
    @classmethod
    def end_after_start(cls, v: datetime, info: Any) -> datetime:
        start = info.data.get("ДатаВремяНачалаСмены")
        if start and v <= start:
            raise ValueError(
                "ДатаВремяОкончанияСмены должна быть позже ДатаВремяНачалаСмены"
            )
        return v


class BatchUpdateRequest(BaseModel):
    """Схема обновления партии (PATCH)."""

    is_closed: bool | None = None
    task_description: str | None = None
    shift: str | None = None
    team: str | None = None

    model_config = {"extra": "forbid"}


class ProductResponse(BaseModel):
    """Продукция внутри ответа партии."""

    id: int
    unique_code: str
    is_aggregated: bool
    aggregated_at: datetime | None

    model_config = {"from_attributes": True}


class WorkCenterResponse(BaseModel):
    """Рабочий центр внутри ответа партии."""

    id: int
    identifier: str
    name: str

    model_config = {"from_attributes": True}


class BatchResponse(BaseModel):
    """Полный ответ по партии."""

    id: int
    is_closed: bool
    closed_at: datetime | None
    task_description: str
    shift: str
    team: str
    batch_number: int
    batch_date: date
    nomenclature: str
    ekn_code: str
    shift_start: datetime
    shift_end: datetime
    work_center: WorkCenterResponse
    products: list[ProductResponse]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BatchListItem(BaseModel):
    """Элемент списка партий (без продукции)."""

    id: int
    is_closed: bool
    closed_at: datetime | None
    batch_number: int
    batch_date: date
    nomenclature: str
    shift: str
    team: str
    work_center: WorkCenterResponse
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchListResponse(BaseModel):
    """Ответ списка партий с пагинацией."""

    items: list[BatchListItem]
    total: int
    offset: int
    limit: int
