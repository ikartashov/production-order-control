"""Интеграционные тесты API партий (batches): полный проход
HTTP -> роутер -> BatchService -> BatchRepository -> реальный Postgres.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.batch import Batch

pytestmark = pytest.mark.asyncio


async def _create_batch(
    client: AsyncClient, batch_payload_factory: Any, **overrides: Any
) -> dict[str, Any]:
    payload = batch_payload_factory(**overrides)
    response = await client.post("/api/v1/batches", json=[payload])
    assert response.status_code == 201, response.text
    return response.json()[0]


class TestCreateBatch:
    async def test_create_batch_returns_201_and_persists_to_db(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_payload_factory: Any,
    ) -> None:
        payload = batch_payload_factory(НомерПартии=910001)

        response = await client.post("/api/v1/batches", json=[payload])

        assert response.status_code == 201, response.text
        body = response.json()[0]
        assert body["batch_number"] == 910001
        assert body["nomenclature"] == payload["Номенклатура"]
        assert body["work_center"]["identifier"] == payload["ИдентификаторРЦ"]
        assert body["is_closed"] is False

        # Проверка напрямую в реальной БД (не через API) — доказывает,
        # что запись действительно долетела до Postgres, а не только
        # вернулась в HTTP-ответе.
        result = await db_session.execute(
            select(Batch).where(Batch.batch_number == 910001)
        )
        db_batch = result.scalar_one()
        assert db_batch.nomenclature == payload["Номенклатура"]
        assert db_batch.task_description == payload["ПредставлениеЗаданияНаСмену"]

    async def test_create_duplicate_batch_returns_409(
        self, client: AsyncClient, batch_payload_factory: Any
    ) -> None:
        payload = batch_payload_factory(НомерПартии=910002)

        first = await client.post("/api/v1/batches", json=[payload])
        assert first.status_code == 201, first.text

        second = await client.post("/api/v1/batches", json=[payload])

        assert second.status_code == 409
        assert "detail" in second.json()


class TestGetBatch:
    async def test_get_batch_by_id_returns_200(
        self, client: AsyncClient, batch_payload_factory: Any
    ) -> None:
        created = await _create_batch(client, batch_payload_factory, НомерПартии=910003)

        response = await client.get(f"/api/v1/batches/{created['id']}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == created["id"]
        assert body["batch_number"] == 910003

    async def test_get_batch_missing_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/batches/999999999")

        assert response.status_code == 404
        assert "detail" in response.json()


class TestListBatches:
    async def test_list_batches_pagination(
        self, client: AsyncClient, batch_payload_factory: Any
    ) -> None:
        numbers = [910010, 910011, 910012]
        for number in numbers:
            await _create_batch(client, batch_payload_factory, НомерПартии=number)

        first_page = await client.get(
            "/api/v1/batches",
            params={"offset": 0, "limit": 2, "batch_date": "2024-01-30"},
        )
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert first_body["limit"] == 2
        assert first_body["offset"] == 0
        assert len(first_body["items"]) == 2
        assert first_body["total"] >= 3

        second_page = await client.get(
            "/api/v1/batches",
            params={"offset": 2, "limit": 2, "batch_date": "2024-01-30"},
        )
        assert second_page.status_code == 200
        second_body = second_page.json()
        assert len(second_body["items"]) >= 1

        returned_numbers = {
            item["batch_number"] for item in first_body["items"] + second_body["items"]
        }
        assert returned_numbers.issuperset(set(numbers))

    async def test_list_batches_filters_by_work_center(
        self, client: AsyncClient, batch_payload_factory: Any
    ) -> None:
        created = await _create_batch(client, batch_payload_factory, НомерПартии=910020)
        work_center_id = created["work_center"]["id"]

        response = await client.get(
            "/api/v1/batches", params={"work_center_id": work_center_id}
        )

        assert response.status_code == 200
        body = response.json()
        assert all(
            item["work_center"]["id"] == work_center_id for item in body["items"]
        )
        assert any(item["batch_number"] == 910020 for item in body["items"])


class TestUpdateBatch:
    async def test_update_batch_closes_it(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        batch_payload_factory: Any,
    ) -> None:
        created = await _create_batch(client, batch_payload_factory, НомерПартии=910030)
        assert created["is_closed"] is False

        response = await client.patch(
            f"/api/v1/batches/{created['id']}", json={"is_closed": True}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["is_closed"] is True
        assert body["closed_at"] is not None

        result = await db_session.execute(
            select(Batch).where(Batch.id == created["id"])
        )
        db_batch = result.scalar_one()
        assert db_batch.is_closed is True
        assert db_batch.closed_at is not None

    async def test_update_batch_missing_returns_404(self, client: AsyncClient) -> None:
        response = await client.patch(
            "/api/v1/batches/999999999", json={"is_closed": True}
        )

        assert response.status_code == 404
