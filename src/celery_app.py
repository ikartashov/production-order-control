"""Инфраструктура Celery: создание приложения и базовая конфигурация."""

from celery import Celery

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
# Будет заполнено в дальнейшем следующими задачами:
#   - auto_close_expired_batches — ежедневно в 01:00
#   - cleanup_old_files — ежедневно в 02:00
#   - update_cached_statistics — каждые 5 минут
#   - retry_failed_webhooks — каждые 15 минут
celery_app.conf.beat_schedule = {}
