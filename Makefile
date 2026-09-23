.PHONY: install run test test-unit test-integration lint format typecheck security \
        precommit migrate migration docker-up docker-down worker beat flower clean

install:  ## Установить зависимости Poetry
	poetry install

run:  ## Запустить API локально (без Docker)
	PYTHONPATH=src poetry run uvicorn main:app --reload

test:  ## Прогнать весь набор тестов (unit + integration)
	poetry run pytest

test-unit:  ## Прогнать только unit-тесты (без реальной БД/Redis)
	poetry run pytest tests/unit

test-integration:  ## Прогнать integration-тесты против localhost Postgres/Redis
	DATABASE_URL=postgresql+asyncpg://postgres:changeme@localhost:5432/production_control \
	REDIS_URL=redis://localhost:6379/0 \
	poetry run pytest tests/integration -v

lint:  ## Проверить стиль кода (ruff)
	poetry run ruff check src/ tests/

format:  ## Отформатировать код (black)
	poetry run black src/ tests/

typecheck:  ## Проверить типы (mypy --strict)
	poetry run mypy src/

security:  ## Проверить код на уязвимости (bandit)
	poetry run bandit -c pyproject.toml -r src/

precommit:  ## Прогнать все pre-commit хуки по всему репозиторию
	poetry run pre-commit run --all-files

migrate:  ## Применить миграции Alembic (upgrade head)
	poetry run alembic upgrade head

migration:  ## Сгенерировать новую миграцию: make migration m="описание"
	poetry run alembic revision --autogenerate -m "$(m)"

docker-up:  ## Поднять весь стек через Docker Compose
	docker compose up --build

docker-down:  ## Остановить Docker Compose стек
	docker compose down

worker:  ## Запустить Celery worker локально
	PYTHONPATH=src poetry run celery -A celery_app worker --loglevel=info

beat:  ## Запустить Celery Beat (планировщик) локально
	PYTHONPATH=src poetry run celery -A celery_app beat --loglevel=info

flower:  ## Запустить Flower (мониторинг Celery) локально
	PYTHONPATH=src poetry run celery -A celery_app flower --port=5555

clean:  ## Удалить кэши/артефакты тестов и линтеров
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
