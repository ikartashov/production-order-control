from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.data.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from src.data.models.product import Product
    from src.data.models.work_center import WorkCenter


class Batch(TimestampMixin, Base):
    """Сменное задание — основная единица производственного планирования.

    Содержит информацию о смене, бригаде, рабочем центре и плановой продукции.
    Партия считается активной пока is_closed=False.
    """

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # --- Статус ---
    is_closed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Признак закрытия партии",
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Дата и время закрытия партии",
    )

    # --- Описание задания ---
    task_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Представление задания на смену (текстовое описание)",
    )
    work_center_id: Mapped[int] = mapped_column(
        ForeignKey("work_centers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="FK на рабочий центр",
    )
    shift: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Номер/название смены (например, '1 смена')",
    )
    team: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Название бригады",
    )

    # --- Идентификация партии ---
    batch_number: Mapped[int] = mapped_column(
        nullable=False,
        index=True,
        comment="Номер партии из внешней системы",
    )
    batch_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Дата партии",
    )

    # --- Продукция ---
    nomenclature: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Наименование номенклатуры",
    )
    ekn_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Код ЕКН (единый классификатор номенклатуры)",
    )

    # --- Временные рамки смены ---
    shift_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Дата и время начала смены",
    )
    shift_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Дата и время окончания смены",
    )

    # Связи
    work_center: Mapped["WorkCenter"] = relationship(
        "WorkCenter",
        back_populates="batches",
        lazy="noload",
    )
    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="batch",
        lazy="noload",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("batch_number", "batch_date", name="uq_batch_number_date"),
        Index("idx_batch_closed", "is_closed"),
        Index("idx_batch_shift_times", "shift_start", "shift_end"),
    )

    def __repr__(self) -> str:
        return (
            f"<Batch id={self.id} "
            f"batch_number={self.batch_number} "
            f"batch_date={self.batch_date} "
            f"is_closed={self.is_closed}>"
        )
