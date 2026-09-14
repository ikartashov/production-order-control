from typing import Any

from fastapi import APIRouter

from api.v1.schemas.task import TaskStatusResponse
from celery_app import celery_app

router = APIRouter(prefix="/tasks", tags=["Задачи"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Получить статус и результат фоновой Celery-задачи по её ID."""
    async_result = celery_app.AsyncResult(task_id)
    task_status: str = async_result.status

    result: dict[str, Any] | None = None
    if task_status == "SUCCESS":
        result = async_result.result
    elif task_status == "PROGRESS":
        result = async_result.info
    elif task_status == "FAILURE":
        result = {"error": str(async_result.result)}

    return TaskStatusResponse(task_id=task_id, status=task_status, result=result)
