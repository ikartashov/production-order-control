from datetime import date

from pydantic import BaseModel


class TodayStats(BaseModel):
    """Показатели дашборда за текущие сутки (UTC)."""

    batches_created: int
    batches_closed: int
    products_added: int
    products_aggregated: int


class ShiftStats(BaseModel):
    """Показатели дашборда в разрезе одной смены."""

    batches: int
    products: int
    aggregated: int


class WorkCenterStats(BaseModel):
    """Показатели дашборда по одному рабочему центру (для топа по партиям)."""

    id: str
    name: str
    batches_count: int
    products_count: int
    aggregation_rate: float


class DashboardSummary(BaseModel):
    """Сводные показатели дашборда."""

    total_batches: int
    active_batches: int
    closed_batches: int
    total_products: int
    aggregated_products: int
    aggregation_rate: float
    today: TodayStats
    by_shift: dict[str, ShiftStats]
    top_work_centers: list[WorkCenterStats]

    model_config = {"extra": "ignore"}


class DashboardStatsResponse(BaseModel):
    """Ответ дашборда аналитики (кешируется на 5 минут)."""

    summary: DashboardSummary
    cached_at: str


class BatchInfo(BaseModel):
    """Идентифицирующая информация о партии."""

    id: int
    batch_number: int
    batch_date: date
    is_closed: bool


class ProductionStats(BaseModel):
    """Статистика производства/агрегации по партии."""

    total_products: int
    aggregated: int
    remaining: int
    aggregation_rate: float


class Timeline(BaseModel):
    """Временные показатели выполнения партии в рамках смены."""

    shift_duration_hours: float
    elapsed_hours: float
    products_per_hour: float
    estimated_completion: str | None


class TeamPerformance(BaseModel):
    """Показатели эффективности бригады, выполняющей партию."""

    team: str
    avg_products_per_hour: float
    efficiency_score: float


class BatchStatisticsResponse(BaseModel):
    """Ответ статистики по одной партии."""

    batch_info: BatchInfo
    production_stats: ProductionStats
    timeline: Timeline
    team_performance: TeamPerformance


class CompareBatchesRequest(BaseModel):
    """Запрос на сравнение нескольких партий."""

    batch_ids: list[int]

    model_config = {"extra": "forbid"}


class BatchComparisonItem(BaseModel):
    """Одна партия в сравнительной таблице."""

    batch_id: int
    batch_number: int
    total_products: int
    aggregated: int
    rate: float
    duration_hours: float
    products_per_hour: float


class ComparisonAverage(BaseModel):
    """Средние показатели по сравниваемым партиям."""

    aggregation_rate: float
    products_per_hour: float


class CompareBatchesResponse(BaseModel):
    """Ответ сравнения партий."""

    comparison: list[BatchComparisonItem]
    average: ComparisonAverage
