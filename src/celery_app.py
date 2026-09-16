"""Инфраструктура Celery: создание приложения и базовая конфигурация."""

from celery import Celery
from celery.schedules import crontab

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "production_control",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "tasks.webhook_tasks",
        "tasks.aggregation_tasks",
        "tasks.report_tasks",
        "tasks.import_tasks",
        "tasks.export_tasks",
        "tasks.scheduled_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)

# Расписание периодических задач (Celery Beat).
celery_app.conf.beat_schedule = {
    "auto-close-expired-batches": {
        "task": "tasks.scheduled_tasks.auto_close_expired_batches",
        "schedule": crontab(hour=1, minute=0),
    },
    "cleanup-old-files": {
        "task": "tasks.scheduled_tasks.cleanup_old_files",
        "schedule": crontab(hour=2, minute=0),
    },
    "update-cached-statistics": {
        "task": "tasks.scheduled_tasks.update_cached_statistics",
        "schedule": crontab(minute="*/5"),
    },
    "retry-failed-webhooks": {
        "task": "tasks.scheduled_tasks.retry_failed_webhooks",
        "schedule": crontab(minute="*/15"),
    },
}
