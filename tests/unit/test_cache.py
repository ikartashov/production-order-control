from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

from core.cache import cache_delete_pattern, cached

CACHE_GET_PATH = "core.cache.cache_get"
CACHE_SET_PATH = "core.cache.cache_set"
GET_REDIS_PATH = "core.cache.get_redis"


class FakeRedis:
    """Фейковый асинхронный Redis-клиент для проверки `cache_delete_pattern`.

    Реализует только то, чем пользуется `cache_delete_pattern`: `scan_iter`
    (асинхронный генератор, как у `redis.asyncio.Redis`) и `delete`. `keys`
    намеренно НЕ реализован — если реализация случайно вернётся к `KEYS`,
    тест упадёт с `AttributeError`, а не тихо пройдёт.
    """

    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self.scan_iter_calls: list[str | None] = []
        self.delete = AsyncMock(return_value=0)

    async def scan_iter(self, match: str | None = None) -> AsyncIterator[str]:
        self.scan_iter_calls.append(match)
        for key in self._keys:
            yield key


class Dummy:
    """Тестовый класс с декорированным асинхронным методом (имитирует сервис)."""

    def __init__(self) -> None:
        self.calls = 0

    @cached(ttl=60, key_prefix="dummy")
    async def compute(self, x: int, y: int = 0) -> dict[str, int]:
        self.calls += 1
        return {"x": x, "y": y, "calls": self.calls}


class TestCachedDecorator:
    """Тесты для декоратора `cached` из core/cache.py."""

    async def test_cache_miss_calls_function_and_stores_result(self) -> None:
        """При промахе кеша вызывается обёрнутая функция, результат сохраняется."""
        mock_cache_get = AsyncMock(return_value=None)
        mock_cache_set = AsyncMock()

        with (
            patch(CACHE_GET_PATH, mock_cache_get),
            patch(CACHE_SET_PATH, mock_cache_set),
        ):
            obj = Dummy()
            result = await obj.compute(1, y=2)

        assert result == {"x": 1, "y": 2, "calls": 1}
        assert obj.calls == 1
        mock_cache_get.assert_awaited_once()
        mock_cache_set.assert_awaited_once()
        # ttl передан как есть
        assert mock_cache_set.call_args.args[2] == 60
        # сохраняемое значение — результат функции
        assert mock_cache_set.call_args.args[1] == result

    async def test_cache_hit_returns_stored_value_without_calling_function(
        self,
    ) -> None:
        """При попадании в кеш функция не вызывается — возвращается сохранённое значение."""
        stored_value = {"x": 1, "y": 2, "calls": 999}
        mock_cache_get = AsyncMock(return_value=stored_value)
        mock_cache_set = AsyncMock()

        with (
            patch(CACHE_GET_PATH, mock_cache_get),
            patch(CACHE_SET_PATH, mock_cache_set),
        ):
            obj = Dummy()
            result = await obj.compute(1, y=2)

        assert result == stored_value
        assert obj.calls == 0
        mock_cache_get.assert_awaited_once()
        mock_cache_set.assert_not_awaited()

    async def test_self_is_excluded_from_cache_key(self) -> None:
        """Разные экземпляры с одинаковыми аргументами используют один и тот же ключ."""
        mock_cache_get = AsyncMock(return_value=None)
        mock_cache_set = AsyncMock()

        with (
            patch(CACHE_GET_PATH, mock_cache_get),
            patch(CACHE_SET_PATH, mock_cache_set),
        ):
            obj_a = Dummy()
            obj_b = Dummy()
            await obj_a.compute(1, y=2)
            await obj_b.compute(1, y=2)

        key_a = mock_cache_get.call_args_list[0].args[0]
        key_b = mock_cache_get.call_args_list[1].args[0]
        assert key_a == key_b
        # self (repr вида "<...Dummy object at 0x...>") не должен попасть в ключ
        assert "Dummy object" not in key_a

    async def test_key_prefix_and_positional_args_are_used(self) -> None:
        """Ключ кеша строится из key_prefix и позиционных/именованных аргументов."""
        mock_cache_get = AsyncMock(return_value=None)
        mock_cache_set = AsyncMock()

        with (
            patch(CACHE_GET_PATH, mock_cache_get),
            patch(CACHE_SET_PATH, mock_cache_set),
        ):
            obj = Dummy()
            await obj.compute(42, y=7)

        cache_key = mock_cache_get.call_args.args[0]
        assert cache_key.startswith("dummy:42")
        assert "7" in cache_key

    async def test_different_args_produce_different_keys(self) -> None:
        """Разные аргументы дают разные ключи кеша (промах для каждого набора)."""
        mock_cache_get = AsyncMock(return_value=None)
        mock_cache_set = AsyncMock()

        with (
            patch(CACHE_GET_PATH, mock_cache_get),
            patch(CACHE_SET_PATH, mock_cache_set),
        ):
            obj = Dummy()
            await obj.compute(1, y=1)
            await obj.compute(2, y=1)

        key_1 = mock_cache_get.call_args_list[0].args[0]
        key_2 = mock_cache_get.call_args_list[1].args[0]
        assert key_1 != key_2

    async def test_functools_wraps_preserves_function_name(self) -> None:
        """functools.wraps сохраняет исходное имя функции у обёртки."""
        assert Dummy.compute.__name__ == "compute"


class TestCacheDeletePattern:
    """Тесты для `cache_delete_pattern` — регресс на замену `KEYS` на `SCAN`."""

    async def test_uses_scan_iter_with_pattern_not_keys(self) -> None:
        """Реализация должна использовать `scan_iter(match=pattern)`, а не блокирующий `KEYS`."""
        fake_redis = FakeRedis(keys=["batches_list:1", "batches_list:2"])

        with patch(GET_REDIS_PATH, AsyncMock(return_value=fake_redis)):
            await cache_delete_pattern("batches_list:*")

        assert fake_redis.scan_iter_calls == ["batches_list:*"]
        fake_redis.delete.assert_awaited_once_with("batches_list:1", "batches_list:2")

    async def test_deletes_only_matching_keys(self) -> None:
        """Ключи, отданные `scan_iter`, удаляются одним вызовом `delete`, лишние — нет."""
        matching = ["batches_list:1", "batches_list:2", "batches_list:3"]
        fake_redis = FakeRedis(keys=matching)

        with patch(GET_REDIS_PATH, AsyncMock(return_value=fake_redis)):
            await cache_delete_pattern("batches_list:*")

        deleted_keys = fake_redis.delete.call_args.args
        assert set(deleted_keys) == set(matching)
        assert len(deleted_keys) == len(matching)

    async def test_no_matching_keys_does_not_call_delete(self) -> None:
        """Если `scan_iter` ничего не вернул, `delete` не вызывается."""
        fake_redis = FakeRedis(keys=[])

        with patch(GET_REDIS_PATH, AsyncMock(return_value=fake_redis)):
            await cache_delete_pattern("nonexistent:*")

        fake_redis.delete.assert_not_awaited()

    async def test_large_key_set_is_deleted_in_chunks(self) -> None:
        """Большое число ключей удаляется пачками (не одним огромным `DEL`)."""
        keys = [f"batches_list:{i}" for i in range(1250)]
        fake_redis = FakeRedis(keys=keys)

        with patch(GET_REDIS_PATH, AsyncMock(return_value=fake_redis)):
            await cache_delete_pattern("batches_list:*")

        # 1250 ключей пачками по 500 -> 3 вызова delete (500, 500, 250)
        assert fake_redis.delete.await_count == 3
        chunk_sizes = [len(call.args) for call in fake_redis.delete.call_args_list]
        assert chunk_sizes == [500, 500, 250]
        assert sum(chunk_sizes) == len(keys)
        all_deleted = {
            key for call in fake_redis.delete.call_args_list for key in call.args
        }
        assert all_deleted == set(keys)

    async def test_no_keys_and_pattern_used_correctly(self) -> None:
        """`scan_iter` вызывается ровно с переданным паттерном, даже при отсутствии совпадений."""
        fake_redis = FakeRedis(keys=[])

        with patch(GET_REDIS_PATH, AsyncMock(return_value=fake_redis)):
            await cache_delete_pattern("webhooks:*")

        assert fake_redis.scan_iter_calls == ["webhooks:*"]
