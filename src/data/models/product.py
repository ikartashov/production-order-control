from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base

if TYPE_CHECKING:
    from src.data.models.batch import Batch


class Product(Base):
    """Единица продукции — конкретный экземпляр, привязанный к партии.

    Уникальный код используется для идентификации при агрегации
    (сканирование штрих-кода / QR-кода на производстве).
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    unique_code: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="Уникальный код продукции (штрих-код / QR-код)",
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK на партию",
    )

    # Агрегация
    is_aggregated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
        comment="Признак агрегации (продукция прошла контроль)",
    )
    aggregated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Дата и время агрегации",
    )

    # Метаданные
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Дата и время создания записи",
    )

    # Связи
    batch: Mapped["Batch"] = relationship(
        "Batch",
        back_populates="products",
        lazy="noload",
    )

    __table_args__ = (
        Index("idx_product_batch_aggregated", "batch_id", "is_aggregated"),
    )

    def __repr__(self) -> str:
        return (
            f"<Product id={self.id} "
            f"unique_code={self.unique_code!r} "
            f"is_aggregated={self.is_aggregated}>"
        )
