from fastapi import APIRouter, Query

from api.v1.schemas.work_center import WorkCenterListResponse, WorkCenterResponse
from core.dependencies import DbSession
from data.models.work_center import WorkCenter
from domain.services.work_center_service import WorkCenterService

router = APIRouter(prefix="/work-centers", tags=["Рабочие центры"])


def _get_service(session: DbSession) -> WorkCenterService:
    return WorkCenterService(session)


@router.get("", response_model=WorkCenterListResponse)
async def list_work_centers(
    session: DbSession,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> WorkCenterListResponse:
    """Список рабочих центров с пагинацией."""
    service = _get_service(session)
    work_centers, total = await service.get_work_centers_list(
        offset=offset, limit=limit
    )
    return WorkCenterListResponse(
        items=[WorkCenterResponse.model_validate(wc) for wc in work_centers],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{work_center_id}", response_model=WorkCenterResponse)
async def get_work_center(work_center_id: int, session: DbSession) -> WorkCenter:
    """Получить рабочий центр по ID."""
    service = _get_service(session)
    return await service.get_work_center(work_center_id)
