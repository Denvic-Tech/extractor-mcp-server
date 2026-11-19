"""Разбор результата операции, сохранённого в DCC, теми же правилами, что и живой ответ 1С."""
from typing import Any

from .errors import DccOperationPending, ProjectsApiError, upstream_failure
from .routes import ROUTES


def relay_result(state: dict[str, Any], route: str) -> tuple[bool, Any]:
    """Разобрать сохранённый в DCC результат: ``(готов, результат 1С)`` или ошибка EPA."""
    if state.get("status") in {"recovery_required", "uncertain"}:
        raise DccOperationPending(state["operation_id"])
    if state.get("status") == "cancelled":
        raise ProjectsApiError(409, "DCC operation cancelled; verify state before retry")
    if state.get("status") not in {"completed", "failed"} or state.get("result_ready") is not True:
        return False, None
    status, result = state.get("http_status"), state.get("result")
    if type(status) is not int or not isinstance(result, (dict, list)):
        raise ProjectsApiError(502, "DCC operation has no structured 1C result")
    if status != 200:
        default = ("1C receiver observation failed" if route == "projects/receiver/inspect"
                   else "1C operation failed; verify state before retry")
        raise upstream_failure(route, status, result, default)
    return True, result


def operation_result(state: dict[str, Any], route: str) -> tuple[bool, Any]:
    """То же, но с проверкой, что запись DCC относится к ожидаемому маршруту."""
    if route not in ROUTES:
        raise ValueError("Unknown API operation")
    if state.get("method") is not None and state["method"] != route:
        raise ProjectsApiError(502, "DCC operation method mismatch")
    return relay_result(state, route)
