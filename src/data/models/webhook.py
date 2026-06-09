from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.data.models.mixins import TimestampMixin

if TYPE_CHECKING:
    pass


class WebhookSubscription(TimestampMixin, Base):
    """Подписка на webhook-события системы.

    Внешние системы регистрируют URL и перечень интересующих событий.
    При наступлении события система отправляет POST-запрос на указанный URL
    с HMAC-подписью для верификации.
    """

    __tablename__ = "webhook_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        comment="URL для отправки webhook-уведомлений",
    )
    events: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        comment="Список событий: batch_created, batch_closed, product_aggregated и т.д.",
    )
    secret_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Секретный ключ для HMAC-подписи тела запроса",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Активна ли подписка",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
        comment="Количество попыток повторной отправки при неудаче",
    )
    timeout: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
        comment="Таймаут запроса в секундах",
    )

    # Связи
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        "WebhookDelivery",
        back_populates="subscription",
        lazy="noload",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookSubscription id={self.id} "
            f"url={self.url!r} "
            f"is_active={self.is_active}>"
        )


class WebhookDelivery(Base):
    """Запись о попытке доставки webhook-уведомления.

    Хранит полный контекст каждой попытки: статус, ответ сервера,
    текст ошибки. Используется для мониторинга и повторной отправки.
    """

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK на подписку",
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Тип события (batch_created, batch_closed и т.д.)",
    )
    payload: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSON,
        nullable=False,
        comment="Тело отправленного запроса (JSON)",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="Статус доставки: pending, success, failed",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Количество выполненных попыток отправки",
    )
    response_status: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="HTTP-статус ответа внешнего сервера",
    )
    response_body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Тело ответа внешнего сервера (первые N символов)",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Текст ошибки при неудачной доставке",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Дата и время создания записи о доставке",
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Дата и время успешной доставки",
    )

    # Связи
    subscription: Mapped["WebhookSubscription"] = relationship(
        "WebhookSubscription",
        back_populates="deliveries",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookDelivery id={self.id} "
            f"event_type={self.event_type!r} "
            f"status={self.status!r} "
            f"attempts={self.attempts}>"
        )
