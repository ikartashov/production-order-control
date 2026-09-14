import os
import tempfile
from datetime import date
from uuid import uuid4

from fastapi import APIRouter, Query, UploadFile, status

from api.v1.schemas.batch import (
    BatchCreateItem,
    BatchListItem,
    BatchListResponse,
    BatchResponse,
    BatchUpdateRequest,
)
from api.v1.schemas.batch_files import (
    AggregateAsyncRequest,
    AggregateAsyncResponse,
    ExportRequest,
    ReportRequest,
    TaskAcceptedResponse,
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
from storage.minio_service import MinIOService
from tasks.aggregation_tasks import aggregate_products_batch
from tasks.export_tasks import export_batches_to_file
from tasks.import_tasks import import_batches_from_file
from tasks.report_tasks import generate_batch_report

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


@router.post(
    "/{batch_id}/aggregate-async",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AggregateAsyncResponse,
)
async def aggregate_products_async(
    batch_id: int,
    payload: AggregateAsyncRequest,
) -> AggregateAsyncResponse:
    """Запустить асинхронную (частично-успешную) агрегацию продукции в партии."""
    task = aggregate_products_batch.delay(batch_id, payload.unique_codes)
    return AggregateAsyncResponse(
        task_id=task.id,
        status="PENDING",
        message="Aggregation task started",
    )


@router.post(
    "/{batch_id}/reports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskAcceptedResponse,
)
async def create_batch_report(
    batch_id: int,
    payload: ReportRequest,
) -> TaskAcceptedResponse:
    """Запустить формирование отчёта по партии (Excel/PDF), загружается в MinIO."""
    task = generate_batch_report.delay(batch_id, payload.format, payload.email)
    return TaskAcceptedResponse(task_id=task.id, status="PENDING")


@router.post(
    "/import",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskAcceptedResponse,
)
async def import_batches(file: UploadFile) -> TaskAcceptedResponse:
    """Загрузить Excel/CSV файл в MinIO и запустить асинхронный импорт партий."""
    suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        temp_path = tmp.name

    try:
        object_name = f"import_{uuid4().hex}{suffix}"
        MinIOService().upload_file(
            bucket="imports", file_path=temp_path, object_name=object_name
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    task = import_batches_from_file.delay(object_name)
    return TaskAcceptedResponse(
        task_id=task.id,
        status="PENDING",
        message="File uploaded, import started",
    )


@router.post(
    "/export",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskAcceptedResponse,
)
async def export_batches(payload: ExportRequest) -> TaskAcceptedResponse:
    """Запустить асинхронный экспорт партий (Excel/CSV) по фильтрам."""
    task = export_batches_to_file.delay(payload.filters, payload.format)
    return TaskAcceptedResponse(task_id=task.id, status="PENDING")
