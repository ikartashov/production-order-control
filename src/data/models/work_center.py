from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.database import Base
from src.data.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from src.data.models.batch import Batch


class WorkCenter(TimestampMixin, Base):
    """Рабочий центр — производственное подразделение (цех, участок и т.д.)."""

    __tablename__ = "work_centers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    identifier: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
        comment="Идентификатор РЦ из внешней системы (например, RC-001)",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Название рабочего центра",
    )

    # Связи
    batches: Mapped[list["Batch"]] = relationship(
        "Batch",
        back_populates="work_center",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<WorkCenter id={self.id} identifier={self.identifier!r} name={self.name!r}>"
