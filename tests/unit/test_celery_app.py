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


def test_celery_app_beat_schedule_empty_for_now() -> None:
    """Расписание Beat пока не содержит задач — они появятся позже."""
    assert celery_app.conf.beat_schedule == {}
