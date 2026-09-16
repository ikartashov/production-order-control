from fastapi import APIRouter

from api.v1.schemas.analytics import (
    CompareBatchesRequest,
    CompareBatchesResponse,
    DashboardStatsResponse,
)
from core.dependencies import DbSession
from domain.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Аналитика"])


def _get_service(session: DbSession) -> AnalyticsService:
    return AnalyticsService(session)


@router.get("/dashboard", response_model=DashboardStatsResponse)
async def get_dashboard_stats(session: DbSession) -> DashboardStatsResponse:
    """Сводная статистика для дашборда (кешируется на 5 минут)."""
    service = _get_service(session)
    stats = await service.get_dashboard_stats()
    return DashboardStatsResponse.model_validate(
        {"summary": stats, "cached_at": stats["cached_at"]}
    )


@router.post("/compare-batches", response_model=CompareBatchesResponse)
async def compare_batches(
    payload: CompareBatchesRequest,
    session: DbSession,
) -> CompareBatchesResponse:
    """Сравнить несколько партий по продуктивности (продукция/час, % агрегации)."""
    service = _get_service(session)
    result = await service.compare_batches(payload.batch_ids)
    return CompareBatchesResponse.model_validate(result)
