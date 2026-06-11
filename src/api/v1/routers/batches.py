from datetime import date

from fastapi import APIRouter, Query, status

from api.v1.schemas.batch import (
    BatchCreateItem,
    BatchListItem,
    BatchListResponse,
    BatchResponse,
    BatchUpdateRequest,
)
from api.v1.schemas.product import (
    AggregateRequest,
    ProductListResponse,
    ProductResponse,
)
from core.dependencies import DbSession
from data.models.batch import Batch
from domain.services.batch_service import BatchService
from domain.services.product_service import ProductService

router = APIRouter(prefix="/batches", tags=["Партии"])


def _get_service(session: DbSession) -> BatchService:
    return BatchService(session)


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=list[BatchResponse]
)
async def create_batches(
    payload: list[BatchCreateItem],
    session: DbSession,
) -> list[Batch]:
    """Создать список сменных заданий (из 1С)."""
    service = _get_service(session)
    items = [
        {
            "is_closed": item.СтатусЗакрытия,
            "task_description": item.ПредставлениеЗаданияНаСмену,
            "wc_name": item.РабочийЦентр,
            "wc_identifier": item.ИдентификаторРЦ,
            "shift": item.Смена,
            "team": item.Бригада,
            "batch_number": item.НомерПартии,
            "batch_date": item.ДатаПартии,
            "nomenclature": item.Номенклатура,
            "ekn_code": item.КодЕКН,
            "shift_start": item.ДатаВремяНачалаСмены,
            "shift_end": item.ДатаВремяОкончанияСмены,
        }
        for item in payload
    ]
    batches = await service.create_batches(items)
    return [await service.get_batch(b.id) for b in batches]


@router.get("", response_model=BatchListResponse)
async def list_batches(
    session: DbSession,
    is_closed: bool | None = Query(default=None),
    batch_number: int | None = Query(default=None),
    batch_date: date | None = Query(default=None),
    work_center_id: int | None = Query(default=None),
    shift: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> BatchListResponse:
    """Список партий с фильтрацией и пагинацией."""
    service = _get_service(session)
    batches, total = await service.get_batches_list(
        {
            "is_closed": is_closed,
            "batch_number": batch_number,
            "batch_date": batch_date,
            "work_center_id": work_center_id,
            "shift": shift,
            "offset": offset,
            "limit": limit,
        }
    )
    return BatchListResponse(
        items=[BatchListItem.model_validate(b) for b in batches],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{batch_id}", response_model=BatchResponse)
async def get_batch(batch_id: int, session: DbSession) -> Batch:
    """Получить партию по ID."""
    service = _get_service(session)
    return await service.get_batch(batch_id)


@router.patch("/{batch_id}", response_model=BatchResponse)
async def update_batch(
    batch_id: int,
    payload: BatchUpdateRequest,
    session: DbSession,
) -> Batch:
    """Обновить партию."""
    service = _get_service(session)
    return await service.update_batch(batch_id, payload.model_dump(exclude_none=True))


@router.post(
    "/{batch_id}/aggregate",
    response_model=ProductListResponse,
)
async def aggregate_products(
    batch_id: int,
    payload: AggregateRequest,
    session: DbSession,
) -> ProductListResponse:
    """Агрегировать продукцию в партии по уникальным кодам."""
    service = ProductService(session)
    products = await service.aggregate_products(batch_id, payload.unique_codes)
    return ProductListResponse(
        items=[ProductResponse.model_validate(p) for p in products],
        total=len(products),
    )
