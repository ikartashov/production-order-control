import time
from collections.abc import Awaitable, Callable

from loguru import logger
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.cache import get_redis

# Значение по умолчанию — 100 запросов в минуту на один IP.
DEFAULT_RATE_LIMIT_REQUESTS = 100
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60

# Пути, исключённые из ограничения (liveness/readiness-проба).
EXEMPT_PATHS = frozenset({"/health"})

RequestResponseEndpoint = Callable[[Request], Awaitable[Response]]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware, реализующее ограничение частоты запросов методом
    скользящего окна (sliding window counter approximation).

    Наивный алгоритм с одним фиксированным окном (``INCR`` по ключу
    ``f"...:{window_start}"``) допускает всплеск до ~2x от заявленного
    лимита, если запросы группируются вокруг границы окна (конец одного
    окна + начало следующего, каждое из которых само по себе укладывается
    в лимит). Чтобы этого избежать, здесь используется стандартная
    приближённая техника — "sliding window counter": помимо счётчика
    *текущего* окна хранится счётчик *предыдущего* окна, и решение
    принимается по взвешенной сумме:

        elapsed_fraction = (now - window_start * window_seconds) / window_seconds
        weighted_count = previous_window_count * (1 - elapsed_fraction) + current_window_count

    То есть чем раньше мы находимся в текущем окне, тем больше веса у
    "хвоста" предыдущего окна — предполагается, что трафик внутри окна
    распределён более-менее равномерно. Это приближение, а не точный
    подсчёт (как у sliding-log с логом временных меток каждого запроса),
    но оно дёшево (два Redis-ключа вместо лога всех запросов) и на
    практике ограничивает худший всплеск величиной, близкой к
    ``limit`` (а не ``2 * limit``, как у фиксированного окна).

    Счётчики хранятся в Redis (тот же клиент/пул, что и в ``core.cache``)
    под ключами ``f"ratelimit:{client_ip}:{window_start_epoch}"``, где
    ``window_start_epoch`` — номер окна (``unix_time // window_seconds``).
    ``INCR`` текущего окна и ``EXPIRE ... NX`` (выставление TTL только если
    у ключа его ещё нет — то есть фактически только на первом запросе
    окна, без лишнего проверяющего round-trip) отправляются вместе с
    ``GET`` счётчика предыдущего окна одним Redis-пайплайном (один
    round-trip вместо нескольких последовательных).

    Важно: middleware наследуется от ``BaseHTTPMiddleware`` и оборачивает
    приложение *снаружи* стандартного стека обработки исключений FastAPI
    (``ExceptionMiddleware``). Поэтому превышение лимита обрабатывается
    здесь напрямую — возвращается готовый ``JSONResponse`` со статусом 429,
    а не исключение (``HTTPException``/``AppError``): исключение, поднятое
    из ``dispatch()``, не проходит через зарегистрированные в
    ``register_exception_handlers`` обработчики и всплывает как
    необработанная ошибка (500) выше по стеку ASGI. Это было проверено
    эмпирически с помощью ``starlette.testclient.TestClient`` — см. отчёт
    в PR/описании задачи.
    """

    def __init__(
        self,
        app: object,
        limit: int = DEFAULT_RATE_LIMIT_REQUESTS,
        window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._limit = limit
        self._window_seconds = window_seconds

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_seconds = self._window_seconds
        window_start = int(now // window_seconds)
        current_key = f"ratelimit:{client_ip}:{window_start}"
        previous_key = f"ratelimit:{client_ip}:{window_start - 1}"

        try:
            redis = await get_redis()
            async with redis.pipeline() as pipe:
                # Один round-trip: инкремент текущего окна, TTL только если
                # его ещё нет у ключа (фактически — только на первом запросе
                # окна, без отдельной проверки), и чтение предыдущего окна.
                pipe.incr(current_key)
                pipe.expire(current_key, window_seconds, nx=True)
                pipe.get(previous_key)
                current_count, _, previous_count_raw = await pipe.execute()
        except RedisError as exc:
            # Redis недоступен — не блокируем трафик из-за инфраструктурной
            # проблемы (fail-open), просто логируем и пропускаем запрос.
            logger.error(
                "Rate limiter: Redis недоступен, запрос пропущен без проверки: {}",
                exc,
            )
            return await call_next(request)

        previous_count = (
            int(previous_count_raw) if previous_count_raw is not None else 0
        )

        # Приближённый подсчёт скользящего окна: чем ближе к началу текущего
        # окна, тем больше веса у "хвоста" предыдущего окна.
        elapsed_fraction = (now - window_start * window_seconds) / window_seconds
        weighted_count = previous_count * (1 - elapsed_fraction) + current_count

        if weighted_count > self._limit:
            logger.warning(
                "Rate limit превышен для {}: {:.2f} > {} за окно {} с",
                client_ip,
                weighted_count,
                self._limit,
                window_seconds,
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )

        return await call_next(request)
