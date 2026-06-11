from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Базовый репозиторий с общими CRUD операциями."""

    def __init__(self, model: type[ModelType], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get_by_id(self, record_id: int) -> ModelType | None:
        """Получить запись по ID."""
        result = await self._session.execute(
            select(self._model).where(self._model.id == record_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs: Any) -> ModelType:
        """Создать запись."""
        instance = self._model(**kwargs)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def delete(self, instance: ModelType) -> None:
        """Удалить запись."""
        await self._session.delete(instance)
        await self._session.flush()
