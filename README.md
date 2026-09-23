# Production Order Control

Бэкенд-система для учёта производственных партий и отдельных изделий внутри
них в цеху: регистрация партий по рабочим центрам и сменам, сканирование и
агрегация отдельных изделий в партии, формирование отчётов, массовый
импорт/экспорт данных партий и уведомление внешних систем о событиях
жизненного цикла партий/изделий через вебхуки. Тяжёлые операции (агрегация,
генерация отчётов, импорт/экспорт, доставка вебхуков) вынесены в фоновые
задачи Celery, чтобы API оставался отзывчивым.

## Технологический стек

- **API**: FastAPI (async), Pydantic v2 / pydantic-settings
- **База данных**: PostgreSQL, SQLAlchemy 2 (async), миграции Alembic
- **Фоновая обработка**: Celery, RabbitMQ (брокер), Celery Beat (задачи по расписанию), Flower (UI мониторинга)
- **Кэш / результаты**: Redis — используется и как кэш приложения (`core/cache.py`), и как result backend для Celery
- **Объектное хранилище**: MinIO (S3-совместимое) — файлы импорта/экспорта партий, сгенерированные отчёты
- **Сборка**: Poetry, плоская структура `src/`
- **Эксплуатация**: Docker Compose для локального запуска всех сервисов

## Установка и запуск

### Требования

- Python 3.12
- [Poetry](https://python-poetry.org/)
- Docker + Docker Compose (для полного стека или если не хотите запускать
  Postgres/Redis/RabbitMQ/MinIO нативно)

### Настройка окружения

Скопируйте пример env-файла и заполните реальными значениями:

```bash
cp .env.example .env
```

**Известная ловушка — `.env.example` и `Settings`:** в `.env.example`
описаны *отдельные* переменные хоста/порта/учётных данных (`POSTGRES_HOST`,
`POSTGRES_PORT`, `REDIS_HOST`, `RABBITMQ_HOST` и т. д.), а готовые URL
приведены только в виде закомментированных примеров. Однако `Settings` в
`src/core/config.py` на самом деле требует **уже собранные поля URL/DSN**
напрямую из окружения — `database_url`, `redis_url`, `celery_broker_url`,
`celery_result_backend` (а также `minio_endpoint`, `minio_access_key`,
`minio_secret_key`, `secret_key`) обязательны, не имеют значений по умолчанию
и *не* собираются из отдельных переменных хоста/порта во время выполнения.
Иными словами, если просто скопировать `.env.example` как есть, при старте
упадёт валидация Pydantic. Пока это не исправлено, добавьте собранные
переменные вручную, например:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:changeme@localhost:5432/production_control
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=amqp://admin:changeme@localhost:5672//
CELERY_RESULT_BACKEND=redis://localhost:6379/1
SECRET_KEY=<не менее 32 символов>
```

(Имена переменных окружения нечувствительны к регистру согласно `Settings.model_config`.)

### Запуск через Docker Compose

```bash
docker compose up --build
```

Поднимаются сервисы: `api` (FastAPI, hot-reload по `src/`), `postgres`,
`redis`, `rabbitmq` (UI управления на `:15672`), `celery_worker`,
`celery_beat`, `flower` (UI мониторинга Celery на `:5555`) и `minio`
(S3 API на `:9000`, консоль на `:9001`). API доступен на `:8000` — Swagger UI
по адресу `http://localhost:8000/docs`.

### Локальный запуск без Docker

```bash
poetry install
poetry run alembic upgrade head
PYTHONPATH=src poetry run uvicorn main:app --reload
```

`PYTHONPATH=src` необходим, потому что проект использует плоскую структуру
`src/` без устанавливаемого пакета — импорты по всему коду указываются без
префикса (`from core.config import get_settings`, а не `from src.core...`).
Это соответствует Docker-образу, где задан `PYTHONPATH=/app/src`, и секции
`[tool.pytest.ini_options]` в `pyproject.toml`, где для тестов задан
`pythonpath = ["src"]`. Также понадобятся Postgres/Redis/RabbitMQ/MinIO,
доступные по адресам из `.env` — либо запустите их через `docker compose up
postgres redis rabbitmq minio`, а API/воркер локально, либо укажите
собственные инстансы.

Чтобы локально запустить и фоновую обработку:

```bash
PYTHONPATH=src poetry run celery -A celery_app worker --loglevel=info
PYTHONPATH=src poetry run celery -A celery_app beat --loglevel=info
```

## Обзор API

Полные схемы запросов/ответов доступны в интерактивном Swagger UI по адресу
`/docs` (или ReDoc по адресу `/redoc`) после запуска API. Все перечисленные
ниже маршруты смонтированы под префиксом `/api/v1`, кроме `/health`.

- **Партии** (`/batches`) — CRUD производственных партий, а также
  `GET /batches/{id}/statistics`, `POST /batches/{id}/aggregate`
  (синхронная агрегация изделий), `POST /batches/{id}/aggregate-async`
  (то же самое через задачу Celery), `POST /batches/{id}/reports`
  (асинхронная генерация отчёта) и массовые эндпоинты `/batches/import` /
  `/batches/export`, работающие через задачи Celery и файловое хранилище MinIO.
- **Изделия** (`/products`) — регистрация изделий в партии.
- **Рабочие центры** (`/work-centers`) — только чтение: список/поиск.
- **Вебхуки** (`/webhooks`) — CRUD подписок на вебхуки и
  `GET /webhooks/{id}/deliveries` для просмотра истории/статуса доставки
  исходящих событий, подписанных HMAC.
- **Задачи** (`/tasks`) — `GET /tasks/{task_id}` для опроса статуса/результата
  асинхронной задачи Celery (агрегация, отчёт, импорт, экспорт).
- **Аналитика** (`/analytics`) — `GET /analytics/dashboard` (кэшированная
  сводная статистика) и `POST /analytics/compare-batches` (сравнение партий по
  производительности/уровню агрегации). *Добавлено совсем недавно вместе с
  этим README — сверьтесь с `/docs`, если что-то здесь выглядит устаревшим.*
- **`/health`** — проба liveness/readiness (проверяет подключение к БД и Redis).
  Не подпадает под ограничение частоты запросов.

**Ограничение частоты запросов**: все маршруты `/api/v1/*` (всё, кроме
`/health`) защищены rate limiter'ом с фиксированным окном на базе Redis —
по умолчанию 100 запросов в минуту на IP клиента. При превышении
возвращается `429 {"detail": "Rate limit exceeded"}`. См.
`src/core/rate_limit.py`.

## Тестирование

```bash
poetry run pytest
```

Запускает набор модульных тестов из `tests/unit/` (сервисы, задачи и rate
limiter покрыты тестами с моками зависимостей — реальные БД/Redis/MinIO не
нужны). Отчёт о покрытии включён по умолчанию через `pyproject.toml`
(`--cov=src --cov-report=term-missing`). Набора `tests/integration/` на
момент написания в репозитории ещё нет (может быть добавлен отдельно).

Установка и запуск pre-commit хуков:

```bash
poetry run pre-commit install
poetry run pre-commit run --all-files
```

Запускаются `ruff` (линтинг), `black` (проверка форматирования), `mypy`
(строгая проверка типов), `bandit` (проверка безопасности), а также
стандартные проверки гигиены (пробелы в конце строк, валидация YAML/TOML,
маркеры merge-конфликтов и т. д.).

## Структура проекта

```
src/
├── main.py                # фабрика create_app(), подключение middleware/роутеров, /health
├── celery_app.py           # экземпляр Celery и регистрация модулей задач
├── api/v1/
│   ├── routers/             # роутеры FastAPI (batches, products, work_centers,
│   │                         #   webhooks, tasks, analytics)
│   └── schemas/              # модели запросов/ответов Pydantic
├── core/                     # сквозная функциональность: конфиг, сессия БД,
│   │                          #   кэш Redis, исключения, rate limiting, DI
├── data/
│   ├── models/                # ORM-модели SQLAlchemy
│   └── repositories/           # слой доступа к данным поверх моделей
├── domain/services/            # бизнес-логика (сервисы партий/изделий/
│   │                            #   рабочих центров/вебхуков/аналитики)
├── storage/                    # клиент MinIO и сервис хранилища
└── tasks/                      # задачи Celery: агрегация, отчёты,
                                  #   импорт/экспорт, вебхуки, задачи по расписанию
```

## Текущее состояние

Проект разрабатывается поэтапно по внутренней спецификации; не все
изначально заявленные возможности обязательно уже реализованы. На момент
написания готовы: API партий/изделий/рабочих центров/вебхуков/задач/аналитики,
асинхронная обработка через Celery, кэширование в Redis, хранилище MinIO,
доставка вебхуков и rate limiter. Если что-то из исходной спецификации здесь
не упомянуто, считайте, что это ещё не реализовано — актуальную информацию
смотрите в `/docs` и в директории роутеров.
