from typing import Any, Literal

from pydantic import BaseModel


class TaskAcceptedResponse(BaseModel):
    """Общий ответ при постановке фоновой задачи в очередь (HTTP 202)."""

    task_id: str
    status: str = "PENDING"
    message: str | None = None


class AggregateAsyncRequest(BaseModel):
    """Схема запроса асинхронной агрегации продукции."""

    unique_codes: list[str]

    model_config = {"extra": "forbid"}


class AggregateAsyncResponse(TaskAcceptedResponse):
    """Ответ на постановку задачи асинхронной агрегации."""


class ReportRequest(BaseModel):
    """Схема запроса на формирование отчёта по партии."""

    format: Literal["excel", "pdf"] = "excel"
    email: str | None = None

    model_config = {"extra": "forbid"}


class ExportRequest(BaseModel):
    """Схема запроса на экспорт партий в файл."""

    format: Literal["excel", "csv"] = "excel"
    filters: dict[str, Any] = {}

    model_config = {"extra": "forbid"}
