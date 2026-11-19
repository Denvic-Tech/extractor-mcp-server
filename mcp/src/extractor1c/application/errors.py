"""Отказы сценариев. Текст каждого предназначен клиенту MCP и не содержит секретов."""
from typing import Any

from ..domain.operations.errors import OperationRejected


class ApplicationError(Exception):
    """Сценарий остановлен; сообщение можно показать пользователю как есть."""


class GatewayError(ApplicationError):
    """Обращение к 1С не удалось. ``status`` — HTTP-статус ответа 1С, если он был."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 error_id: str | None = None):
        self.status = status
        self.code = code
        self.error_id = error_id
        super().__init__(message)


class LicenseRejected(GatewayError):
    """1С подтвердила отказ до любой записи проекта или таблицы."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        super().__init__(str(OperationRejected(result)), status=result.get("httpStatus"),
                         code=result.get("code"), error_id=result.get("errorId"))
