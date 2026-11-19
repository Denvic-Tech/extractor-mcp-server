"""Правила журнала операций: повтор, неопределённый исход и взаимные блокировки.

Функции чистые: хранилище передаёт им уже прочитанные записи и применяет
решение внутри одной транзакции.
"""
from typing import Any

from .errors import OperationIndeterminate, OperationRejected

LAUNCH_ACTIONS = frozenset({"export", "initialization"})


def settled_result(status: str, result: Any) -> Any:
    """Исход повторного запроса с уже известным operationId.

    Завершённая операция воспроизводится без обращения к 1С. Подтверждённый
    отказ повторяется как отказ. Любой другой статус означает неизвестный исход:
    запись нельзя ни повторить, ни заменить новой.
    """
    if status == "completed":
        return result
    if status == "rejected":
        raise OperationRejected(result)
    raise OperationIndeterminate(
        "Operation outcome is indeterminate; verify project state before any new write"
    )


def _same(left: dict[str, Any], right: dict[str, Any], key: str) -> bool:
    return left[key].casefold() == right[key].casefold()


def require_no_unresolved_launch(action: str, payload: dict[str, Any],
                                 unresolved: list[dict[str, Any]]) -> None:
    """Проект с неразрешённым запуском выгрузки или инициализации не принимает новый запуск.

    Новый operationId не обходит блокировку: неизвестный старт сначала сверяют.
    """
    if action not in LAUNCH_ACTIONS:
        return
    if any(_same(other, payload, "projectGuid") for other in unresolved):
        raise OperationIndeterminate(
            "An unresolved export or initialization launch exists for this project; reconcile it before another start"
        )


def require_no_pending_cancellation(payload: dict[str, Any], pending: list[dict[str, Any]]) -> None:
    """Не накладывать запросы остановки одной задачи, пока предыдущий ещё отправляется.

    Неопределённый сигнал остановки можно запросить снова только через новый
    предпросмотр: EPA перепроверяет точное состояние задачи под её блокировкой.
    """
    if any(_same(other, payload, "taskId") and _same(other, payload, "projectGuid") for other in pending):
        raise OperationIndeterminate("An unresolved cancellation exists for this task")
