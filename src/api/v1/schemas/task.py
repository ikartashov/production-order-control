from typing import Any

from pydantic import BaseModel


class TaskStatusResponse(BaseModel):
    """Ответ о статусе фоновой Celery-задачи."""

    task_id: str
    status: str
    result: dict[str, Any] | None = None
