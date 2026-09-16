from celery import Celery

from celery_app import celery_app


def test_celery_app_is_celery_instance() -> None:
    """celery_app должен быть импортируемым экземпляром Celery."""
    assert isinstance(celery_app, Celery)


def test_celery_app_broker_and_backend_configured() -> None:
    """Брокер и backend результатов должны быть заполнены из настроек."""
    assert isinstance(celery_app.conf.broker_url, str)
    assert celery_app.conf.broker_url
    assert isinstance(celery_app.conf.result_backend, str)
    assert celery_app.conf.result_backend


def test_celery_app_beat_schedule_has_four_scheduled_tasks() -> None:
    """Расписание Beat содержит все 4 периодические задачи."""
    beat_schedule = celery_app.conf.beat_schedule
    assert set(beat_schedule.keys()) == {
        "auto-close-expired-batches",
        "cleanup-old-files",
        "update-cached-statistics",
        "retry-failed-webhooks",
    }
    assert (
        beat_schedule["auto-close-expired-batches"]["task"]
        == "tasks.scheduled_tasks.auto_close_expired_batches"
    )
    assert (
        beat_schedule["cleanup-old-files"]["task"]
        == "tasks.scheduled_tasks.cleanup_old_files"
    )
    assert (
        beat_schedule["update-cached-statistics"]["task"]
        == "tasks.scheduled_tasks.update_cached_statistics"
    )
    assert (
        beat_schedule["retry-failed-webhooks"]["task"]
        == "tasks.scheduled_tasks.retry_failed_webhooks"
    )
