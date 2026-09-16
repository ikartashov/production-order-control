"""Интеграционные тесты API рабочих центров (work-centers): список/деталь,
через полный стек до реального Postgres.

Отдельного эндпоинта создания рабочего центра нет — они создаются
неявно (``WorkCenterRepository.get_or_create``) при создании партии
(``POST /api/v1/batches``), поэтому тесты сначала создают партию через
API партий, а затем проверяют её рабочий центр через API рабочих центров.
"""

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _create_batch_with_work_center(
    client: AsyncClient, batch_payload_factory: Any, **overrides: Any
) -> dict[str, Any]:
    payload = batch_payload_factory(**overrides)
    response = await client.post("/api/v1/batches", json=[payload])
    assert response.status_code == 201, response.text
    return response.json()[0]["work_center"]


class TestListWorkCenters:
    async def test_list_work_centers_includes_created_one(
        self, client: AsyncClient, batch_payload_factory: Any
    ) -> None:
        work_center = await _create_batch_with_work_center(
            client,
            batch_payload_factory,
            НомерПартии=920001,
            ИдентификаторРЦ="RC-INTEGRATION-1",
            РабочийЦентр="Интеграционный цех",
        )

        response = await client.get("/api/v1/work-centers", params={"limit": 100})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        ids = {item["id"] for item in body["items"]}
        assert work_center["id"] in ids
        matching = next(
            item for item in body["items"] if item["id"] == work_center["id"]
        )
        assert matching["identifier"] == "RC-INTEGRATION-1"
        assert matching["name"] == "Интеграционный цех"

    async def test_list_work_centers_pagination_shape(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(
            "/api/v1/work-centers", params={"offset": 0, "limit": 5}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["offset"] == 0
        assert body["limit"] == 5
        assert isinstance(body["items"], list)
        assert len(body["items"]) <= 5


class TestGetWorkCenter:
    async def test_get_work_center_by_id_returns_200(
        self, client: AsyncClient, batch_payload_factory: Any
    ) -> None:
        work_center = await _create_batch_with_work_center(
            client,
            batch_payload_factory,
            НомерПартии=920002,
            ИдентификаторРЦ="RC-INTEGRATION-2",
        )

        response = await client.get(f"/api/v1/work-centers/{work_center['id']}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == work_center["id"]
        assert body["identifier"] == "RC-INTEGRATION-2"
        assert "created_at" in body
        assert "updated_at" in body

    async def test_get_work_center_missing_returns_404(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/work-centers/999999999")

        assert response.status_code == 404
        assert "detail" in response.json()
