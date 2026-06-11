from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from loguru import logger


class AppError(Exception):
    """Базовый класс для всех ошибок уровня приложения."""

    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(AppError):
    """Запрашиваемый ресурс не найден."""

    http_status = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """Операция нарушает ограничение уникальности."""

    http_status = status.HTTP_409_CONFLICT


class DomainValidationError(AppError):
    """Нарушение бизнес-правил валидации (отдельно от валидации Pydantic)."""

    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY


class ForbiddenError(AppError):
    """У вызывающей стороны нет прав на выполнение операции."""

    http_status = status.HTTP_403_FORBIDDEN


class ServiceUnavailableError(AppError):
    """Внешняя зависимость (БД, Redis и т.д.) недоступна."""

    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class ValidationError(AppError):
    """Ошибка валидации бизнес-логики."""

    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY


def register_exception_handlers(app: FastAPI) -> None:
    """Регистрирует все обработчики исключений в приложении FastAPI."""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        """Преобразует любой подкласс AppError в JSON HTTP-ответ."""
        logger.debug("AppError {}: {}", exc.__class__.__name__, exc.detail)
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": exc.detail},
        )

    logger.info("Обработчики исключений зарегистрированы")
