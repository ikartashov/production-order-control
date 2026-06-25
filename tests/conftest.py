import copy
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from data.models.batch import Batch
from data.models.product import Product
from data.models.work_center import WorkCenter

BATCH_REPO_PATH = "domain.services.batch_service.BatchRepository"
WC_REPO_PATH = "domain.services.batch_service.WorkCenterRepository"
PRODUCT_REPO_PATH = "domain.services.product_service.ProductRepository"
PRODUCT_BATCH_REPO_PATH = "domain.services.product_service.BatchRepository"


# Фабрики входных данных


def make_batch_dict(**kwargs: Any) -> dict[str, Any]:
    """Фабрика dict для BatchService.create_batches."""
    defaults: dict = {
        "is_closed": False,
        "task_description": "Изготовить 1000 болтов М10",
        "wc_name": "Цех №1",
        "wc_identifier": "RC-001",
        "shift": "1 смена",
        "team": "Бригада Иванова",
        "batch_number": 22222,
        "batch_date": date(2024, 1, 30),
        "nomenclature": "Болт М10х50",
        "ekn_code": "EKN-12345",
        "shift_start": datetime(2024, 1, 30, 8, 0, 0),
        "shift_end": datetime(2024, 1, 30, 20, 0, 0),
    }
    defaults.update(kwargs)
    return defaults


def make_product_dict(unique_code: str = "CODE001", batch_id: int = 1) -> dict:
    """Фабрика dict для ProductService.add_products."""
    return {"unique_code": unique_code, "batch_id": batch_id}


# Контекстные менеджеры для патчинга репозиториев


@contextmanager
def patch_batch_service_repos(
    mock_batch_repo: AsyncMock, mock_wc_repo: AsyncMock
) -> Iterator[None]:
    """Патчит оба репозитория BatchService одновременно."""
    with (
        patch(BATCH_REPO_PATH, return_value=mock_batch_repo),
        patch(WC_REPO_PATH, return_value=mock_wc_repo),
    ):
        yield


@contextmanager
def patch_product_service_repos(
    mock_product_repo: AsyncMock, mock_batch_repo: AsyncMock
):
    """Патчит оба репозитория ProductService одновременно."""
    with (
        patch(PRODUCT_REPO_PATH, return_value=mock_product_repo),
        patch(PRODUCT_BATCH_REPO_PATH, return_value=mock_batch_repo),
    ):
        yield


# Фикстуры сессии


@pytest.fixture
def mock_session() -> AsyncMock:
    """Мок AsyncSession."""
    return AsyncMock()


# Фикстуры доменных объектов


@pytest.fixture
def work_center() -> WorkCenter:
    """Тестовый рабочий центр."""
    wc = WorkCenter()
    wc.id = 1
    wc.identifier = "RC-001"
    wc.name = "Цех №1"
    wc.created_at = datetime(2024, 1, 30, 8, 0, 0)
    wc.updated_at = datetime(2024, 1, 30, 8, 0, 0)
    return wc


@pytest.fixture
def batch(work_center: WorkCenter) -> Batch:
    """Тестовая открытая партия."""
    b = Batch()
    b.id = 1
    b.is_closed = False
    b.closed_at = None
    b.task_description = "Изготовить 1000 болтов М10"
    b.work_center_id = work_center.id
    b.work_center = work_center
    b.shift = "1 смена"
    b.team = "Бригада Иванова"
    b.batch_number = 22222
    b.batch_date = date(2024, 1, 30)
    b.nomenclature = "Болт М10х50"
    b.ekn_code = "EKN-12345"
    b.shift_start = datetime(2024, 1, 30, 8, 0, 0)
    b.shift_end = datetime(2024, 1, 30, 20, 0, 0)
    b.created_at = datetime(2024, 1, 30, 8, 0, 0)
    b.updated_at = datetime(2024, 1, 30, 8, 0, 0)
    b.products = []
    return b


@pytest.fixture
def closed_batch(batch: Batch) -> Batch:
    """Тестовая закрытая партия."""
    b = copy.copy(batch)
    b.is_closed = True
    b.closed_at = datetime(2024, 1, 30, 20, 0, 0)
    return b


@pytest.fixture
def product(batch: Batch) -> Product:
    """Тестовый продукт (не агрегирован)."""
    p = Product()
    p.id = 1
    p.unique_code = "CODE001"
    p.batch_id = batch.id
    p.is_aggregated = False
    p.aggregated_at = None
    p.created_at = datetime(2024, 1, 30, 10, 0, 0)
    p.batch = batch
    return p


@pytest.fixture
def aggregated_product(product: Product) -> Product:
    """Тестовый продукт (уже агрегирован)."""
    p = copy.copy(product)
    p.is_aggregated = True
    p.aggregated_at = datetime(2024, 1, 30, 12, 0, 0)
    return p
