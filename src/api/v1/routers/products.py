from fastapi import APIRouter, status

from api.v1.schemas.product import (
    ProductCreateItem,
    ProductListResponse,
    ProductResponse,
)
from core.dependencies import DbSession
from domain.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["Продукция"])


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=ProductListResponse
)
async def add_products(
    payload: list[ProductCreateItem],
    session: DbSession,
) -> ProductListResponse:
    """Добавить продукцию в партии."""
    service = ProductService(session)
    items = [item.model_dump() for item in payload]
    products = await service.add_products(items)
    return ProductListResponse(
        items=[ProductResponse.model_validate(p) for p in products],
        total=len(products),
    )
