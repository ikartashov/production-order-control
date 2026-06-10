from pydantic import BaseModel


class PaginationParams(BaseModel):
    """Параметры пагинации."""

    offset: int = 0
    limit: int = 20

    model_config = {"extra": "forbid"}
