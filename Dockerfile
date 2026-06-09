# ── Stage 1: builder ─────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install poetry==1.8.5

# Копируем только манифест — кэшируем слой зависимостей отдельно от кода
COPY pyproject.toml poetry.lock* ./

RUN poetry config virtualenvs.in-project true \
    && poetry install --no-root --only main --no-interaction --no-ansi

# ── Stage 2: runtime ─────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app"

WORKDIR /app

# Копируем виртуальное окружение из builder
COPY --from=builder /app/.venv .venv

# Копируем исходный код
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini* ./

EXPOSE 8000

# Команда по умолчанию — переопределяется в docker-compose.yml
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
