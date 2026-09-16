# Production Order Control

A backend system for tracking production batches and the individual products
inside them on a factory floor: registering batches per work center/shift,
scanning and aggregating individual products into batches, generating
reports, importing/exporting batch data in bulk, and notifying external
systems of batch/product lifecycle events via webhooks. Heavier work
(aggregation, report generation, import/export, webhook delivery) is offloaded
to background Celery tasks so the API stays responsive.

## Tech stack

- **API**: FastAPI (async), Pydantic v2 / pydantic-settings
- **Database**: PostgreSQL, SQLAlchemy 2 (async), Alembic migrations
- **Background processing**: Celery, RabbitMQ (broker), Celery Beat (scheduled tasks), Flower (monitoring UI)
- **Cache / results**: Redis — used both as an application cache (`core/cache.py`) and as the Celery result backend
- **Object storage**: MinIO (S3-compatible) — batch import/export files, generated reports
- **Packaging**: Poetry, flat `src/` layout
- **Ops**: Docker Compose for local multi-service orchestration

## Setup & running

### Prerequisites

- Python 3.12
- [Poetry](https://python-poetry.org/)
- Docker + Docker Compose (for the full stack, or if you don't want to run
  Postgres/Redis/RabbitMQ/MinIO natively)

### Environment configuration

Copy the example env file and fill in real values:

```bash
cp .env.example .env
```

**Known gotcha — `.env.example` vs `Settings`:** `.env.example` documents
*discrete* host/port/credential variables (`POSTGRES_HOST`, `POSTGRES_PORT`,
`REDIS_HOST`, `RABBITMQ_HOST`, etc.) and shows the composed URLs only as
commented-out examples. But `Settings` in `src/core/config.py` actually
requires the **pre-composed URL/DSN fields** directly from the environment —
`database_url`, `redis_url`, `celery_broker_url`, `celery_result_backend`
(plus `minio_endpoint`, `minio_access_key`, `minio_secret_key`, `secret_key`)
are all required with no defaults and are *not* assembled from the discrete
host/port vars at runtime. In other words, simply copying `.env.example` as-is
will fail Pydantic validation at startup. Until this is reconciled, you need
to add the composed variables yourself, e.g.:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:changeme@localhost:5432/production_control
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=amqp://admin:changeme@localhost:5672//
CELERY_RESULT_BACKEND=redis://localhost:6379/1
SECRET_KEY=<at least 32 characters>
```

(Env var names are case-insensitive per `Settings.model_config`.)

### Running via Docker Compose

```bash
docker compose up --build
```

This brings up: `api` (FastAPI, hot-reload on `src/`), `postgres`, `redis`,
`rabbitmq` (with management UI on `:15672`), `celery_worker`, `celery_beat`,
`flower` (Celery monitoring UI on `:5555`), and `minio` (S3 API on `:9000`,
console on `:9001`). The API is served on `:8000` — Swagger UI at
`http://localhost:8000/docs`.

### Running locally without Docker

```bash
poetry install
poetry run alembic upgrade head
PYTHONPATH=src poetry run uvicorn main:app --reload
```

The `PYTHONPATH=src` (or `src` layout equivalent) is required because the
project uses a flat `src/` layout without an installable package — imports
throughout the codebase are unprefixed (`from core.config import
get_settings`, not `from src.core...`). This matches the Docker image, which
sets `PYTHONPATH=/app/src`, and `pyproject.toml`'s
`[tool.pytest.ini_options]`, which sets `pythonpath = ["src"]` for tests.
You'll also need Postgres/Redis/RabbitMQ/MinIO reachable at whatever you put
in `.env` — either run them via `docker compose up postgres redis rabbitmq
minio` and only the API/worker locally, or point at your own instances.

To also run background processing locally:

```bash
PYTHONPATH=src poetry run celery -A celery_app worker --loglevel=info
PYTHONPATH=src poetry run celery -A celery_app beat --loglevel=info
```

## API overview

Full request/response schemas are available via the interactive Swagger UI
at `/docs` (or ReDoc at `/redoc`) once the API is running. All routes below
are mounted under the `/api/v1` prefix except `/health`.

- **Batches** (`/batches`) — CRUD for production batches, plus
  `GET /batches/{id}/statistics`, `POST /batches/{id}/aggregate`
  (synchronous product aggregation), `POST /batches/{id}/aggregate-async`
  (same, via Celery task), `POST /batches/{id}/reports` (generate a report,
  async), and bulk `/batches/import` / `/batches/export` endpoints backed by
  Celery tasks and MinIO file storage.
- **Products** (`/products`) — register products against a batch.
- **Work centers** (`/work-centers`) — read-only listing/lookup.
- **Webhooks** (`/webhooks`) — CRUD for webhook subscriptions and
  `GET /webhooks/{id}/deliveries` to inspect delivery history/status of
  HMAC-signed outbound events.
- **Tasks** (`/tasks`) — `GET /tasks/{task_id}` to poll the status/result of
  an async Celery task (aggregation, report, import, export).
- **Analytics** (`/analytics`) — `GET /analytics/dashboard` (cached summary
  stats) and `POST /analytics/compare-batches` (compare batches by
  throughput/aggregation rate). *Landed very recently alongside this README —
  double-check `/docs` if something here looks stale.*
- **`/health`** — liveness/readiness probe (checks DB + Redis connectivity).
  Exempt from rate limiting.

**Rate limiting**: all `/api/v1/*` routes (everything except `/health`) are
protected by a Redis-backed fixed-window rate limiter — 100 requests/minute
per client IP by default. Exceeding it returns `429 {"detail": "Rate limit
exceeded"}`. See `src/core/rate_limit.py`.

## Testing

```bash
poetry run pytest
```

Runs the unit test suite under `tests/unit/` (services, tasks, and the rate
limiter are covered with mocked dependencies — no real DB/Redis/MinIO
required). Coverage reporting is on by default via `pyproject.toml`
(`--cov=src --cov-report=term-missing`). There is no `tests/integration/`
suite in the tree yet as of this writing (may be added separately).

Install and run the pre-commit hooks:

```bash
poetry run pre-commit install
poetry run pre-commit run --all-files
```

This runs `ruff` (lint), `black` (format check), `mypy` (strict type
checking), `bandit` (security linting), plus standard hygiene checks
(trailing whitespace, YAML/TOML validation, merge-conflict markers, etc.).

## Project structure

```
src/
├── main.py                # create_app() factory, middleware/router wiring, /health
├── celery_app.py           # Celery app instance & task module registration
├── api/v1/
│   ├── routers/             # FastAPI routers (batches, products, work_centers,
│   │                         #   webhooks, tasks, analytics)
│   └── schemas/              # Pydantic request/response models
├── core/                     # cross-cutting concerns: config, db session,
│   │                          #   Redis cache, exceptions, rate limiting, DI
├── data/
│   ├── models/                # SQLAlchemy ORM models
│   └── repositories/           # data-access layer over the models
├── domain/services/            # business logic (batch/product/work-center/
│   │                            #   webhook/analytics services)
├── storage/                    # MinIO client & storage service
└── tasks/                      # Celery tasks: aggregation, reports,
                                  #   import/export, webhooks, scheduled tasks
```

## Status notes

This project is being built incrementally against an internal spec; not
every originally-specced feature is necessarily implemented yet. As of this
writing: batches/products/work-centers/webhooks/tasks/analytics APIs, async
Celery processing, Redis caching, MinIO storage, webhook delivery, and this
rate limiter are in place. If something described in the original spec isn't
listed above, treat it as not yet built — check `/docs` and the routers
directory for the current source of truth.
