from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.rate_limit import RateLimitMiddleware

RATE_LIMIT_GET_REDIS_PATH = "core.rate_limit.get_redis"


class FakePipeline:
    """Заменитель ``redis.asyncio.client.Pipeline`` для тестов.

    Буферизует команды (``incr``/``expire``/``get``) так же, как настоящий
    пайплайн redis-py — они выполняются не сразу, а только по ``execute()``,
    который возвращает список результатов в порядке постановки команд.
    Это позволяет проверить в тестах, что ``RateLimitMiddleware`` действительно
    шлёт один round-trip (``execute()`` вызывается один раз на запрос), а не
    несколько последовательных ``await redis.<cmd>()``.
    """

    def __init__(self, redis: "FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def incr(self, key: str) -> "FakePipeline":
        self._ops.append(("incr", (key,), {}))
        return self

    def expire(self, key: str, ttl: int, nx: bool = False) -> "FakePipeline":
        self._ops.append(("expire", (key, ttl), {"nx": nx}))
        return self

    def get(self, key: str) -> "FakePipeline":
        self._ops.append(("get", (key,), {}))
        return self

    async def execute(self) -> list[Any]:
        self._redis.execute_calls += 1
        results: list[Any] = []
        for name, args, kwargs in self._ops:
            method = getattr(self._redis, f"_{name}")
            results.append(await method(*args, **kwargs))
        self._ops = []
        return results

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class FakeRedis:
    """Простой in-memory заменитель Redis-клиента для тестов.

    Реализует то подмножество API (``incr``/``expire``/``get``/``pipeline``),
    которое использует ``RateLimitMiddleware`` — этого достаточно, чтобы
    проверить поведение лимитера, не поднимая настоящий Redis.
    """

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self.execute_calls = 0

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def _incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def _expire(self, key: str, ttl: int, nx: bool = False) -> bool:
        if nx and key in self.expirations:
            return False
        self.expirations[key] = ttl
        return True

    async def _get(self, key: str) -> str | None:
        if key not in self.counters:
            return None
        return str(self.counters[key])


def build_app() -> FastAPI:
    """Минимальное FastAPI-приложение только с лимитером и тестовыми маршрутами."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limit=3, window_seconds=60)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "pong"}

    return app


@contextmanager
def patch_get_redis(fake_redis: FakeRedis) -> Iterator[None]:
    """Патчит ``core.rate_limit.get_redis``, чтобы отдавать ``fake_redis``."""
    with patch(RATE_LIMIT_GET_REDIS_PATH, new=AsyncMock(return_value=fake_redis)):
        yield


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
async def client(fake_redis: FakeRedis) -> AsyncClient:
    app = build_app()
    with patch_get_redis(fake_redis):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac


class TestRateLimitMiddleware:
    """Тесты для RateLimitMiddleware (лимит=3 запроса/окно в тестах)."""

    async def test_requests_under_limit_pass_through(self, client: AsyncClient) -> None:
        """Запросы в пределах лимита проходят без ограничения."""
        for _ in range(3):
            response = await client.get("/ping")
            assert response.status_code == 200
            assert response.json() == {"status": "pong"}

    async def test_request_crossing_limit_gets_429(self, client: AsyncClient) -> None:
        """Первый запрос, превышающий лимит, получает 429 с ожидаемым телом."""
        for _ in range(3):
            response = await client.get("/ping")
            assert response.status_code == 200

        response = await client.get("/ping")
        assert response.status_code == 429
        assert response.json() == {"detail": "Rate limit exceeded"}

    async def test_health_is_exempt_from_rate_limiting(
        self, client: AsyncClient
    ) -> None:
        """/health не учитывается лимитером, сколько бы раз его ни запросили."""
        for _ in range(10):
            response = await client.get("/health")
            assert response.status_code == 200

    async def test_counters_scoped_per_ip(self, fake_redis: FakeRedis) -> None:
        """Два разных IP не делят один и тот же счётчик лимита."""
        app = build_app()
        with patch_get_redis(fake_redis):
            transport = ASGITransport(app=app, client=("9.9.9.9", 1))
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client_a:
                for _ in range(3):
                    response = await client_a.get("/ping")
                    assert response.status_code == 200
                # 4-й запрос с этого же IP должен быть отклонён.
                response = await client_a.get("/ping")
                assert response.status_code == 429

            # Другой IP ещё не исчерпал свой (отдельный) лимит.
            transport_b = ASGITransport(app=app, client=("8.8.8.8", 1))
            async with AsyncClient(
                transport=transport_b, base_url="http://testserver"
            ) as client_b:
                response = await client_b.get("/ping")
                assert response.status_code == 200

    async def test_counters_scoped_per_window(self, fake_redis: FakeRedis) -> None:
        """Разные окна времени используют разные ключи Redis (не делят счётчик).

        Реальные часы не годятся для детерминированной проверки перехода
        между окнами в пределах быстрого юнит-теста, поэтому ``time.time``
        мокается явно: сначала эмулируем окно, полностью исчерпавшее лимит,
        затем — окно через одно от него (``+ 2 * window_seconds``, чтобы у
        "предыдущего" окна для sliding-window-подсчёта не осталось трафика и
        4-е окно не подмешало вес из старого), где счётчик должен начаться
        заново.
        """
        app = build_app()
        window_seconds = 60

        with (
            patch_get_redis(fake_redis),
            patch("core.rate_limit.time.time", return_value=0.0),
        ):
            transport = ASGITransport(app=app, client=("5.5.5.5", 1))
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client_ok:
                for _ in range(3):
                    response = await client_ok.get("/ping")
                    assert response.status_code == 200
                # 4-й запрос в том же окне превышает лимит.
                response = await client_ok.get("/ping")
                assert response.status_code == 429

        assert len(fake_redis.counters) == 1
        old_key = next(iter(fake_redis.counters))
        assert fake_redis.counters[old_key] == 4
        assert fake_redis.expirations[old_key] == window_seconds

        # Окно через одно от исчерпанного — тот же IP, новый ключ Redis,
        # счётчик с нуля, а "предыдущее" окно (window_start - 1) для этого
        # момента ещё не существует в Redis, так что не добавляет веса.
        with (
            patch_get_redis(fake_redis),
            patch(
                "core.rate_limit.time.time",
                return_value=float(2 * window_seconds),
            ),
        ):
            transport2 = ASGITransport(app=app, client=("5.5.5.5", 1))
            async with AsyncClient(
                transport=transport2, base_url="http://testserver"
            ) as client_new_window:
                response = await client_new_window.get("/ping")
                assert response.status_code == 200

        assert len(fake_redis.counters) == 2
        new_key = next(k for k in fake_redis.counters if k != old_key)
        assert new_key != old_key
        assert fake_redis.counters[new_key] == 1
        assert fake_redis.counters[old_key] == 4  # старое окно не тронуто

    async def test_burst_across_window_boundary_stays_near_limit(
        self, fake_redis: FakeRedis
    ) -> None:
        """Всплеск запросов вокруг границы окна не проходит ~2x лимита.

        Раньше (наивное фиксированное окно) можно было исчерпать весь лимит
        в последнюю секунду одного окна и весь лимит заново в первую секунду
        следующего — итого почти 2x лимита за ~1 секунду реального времени.
        Со sliding-window-подсчётом это должно быть ограничено значением,
        близким к лимиту (здесь limit=3): 3 запроса под конец первого окна +
        запросы в начале следующего не должны все проходить.
        """
        app = build_app()
        window_seconds = 60

        accepted = 0

        # Последняя секунда первого окна (window_start=0): elapsed_fraction
        # там близок к 1, поэтому предыдущее окно (которого ещё нет) не
        # играет роли — доступны все 3 слота лимита.
        with (
            patch_get_redis(fake_redis),
            patch(
                "core.rate_limit.time.time",
                return_value=float(window_seconds - 1),
            ),
        ):
            transport = ASGITransport(app=app, client=("7.7.7.7", 1))
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client_first:
                for _ in range(3):
                    response = await client_first.get("/ping")
                    if response.status_code == 200:
                        accepted += 1

        # Первая секунда следующего окна (window_start=1): elapsed_fraction
        # близок к 0, поэтому почти весь вес предыдущего (полностью
        # исчерпанного, count=3) окна учитывается — новых слотов почти нет.
        with (
            patch_get_redis(fake_redis),
            patch(
                "core.rate_limit.time.time",
                return_value=float(window_seconds + 1),
            ),
        ):
            transport2 = ASGITransport(app=app, client=("7.7.7.7", 1))
            async with AsyncClient(
                transport=transport2, base_url="http://testserver"
            ) as client_second:
                for _ in range(3):
                    response = await client_second.get("/ping")
                    if response.status_code == 200:
                        accepted += 1

        # Наивное фиксированное окно пропустило бы все 6 (2x лимита).
        # Sliding-window-подсчёт должен держаться близко к лимиту (3), а не
        # к 2x лимита (6).
        assert accepted < 2 * 3
        assert accepted <= 4

    async def test_uses_single_pipeline_round_trip_per_request(
        self, client: AsyncClient, fake_redis: FakeRedis
    ) -> None:
        """Лимитер шлёт INCR+EXPIRE NX+GET одним пайплайном, а не отдельными вызовами."""
        response = await client.get("/ping")
        assert response.status_code == 200
        assert fake_redis.execute_calls == 1

        response = await client.get("/ping")
        assert response.status_code == 200
        assert fake_redis.execute_calls == 2
