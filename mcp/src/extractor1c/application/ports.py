"""Порты приложения: что сценариям нужно от внешнего мира.

Сценарии зависят только от этих протоколов. Реализации (HTTP 1С, DCC, SQLite)
живут в ``infrastructure`` и подключаются слоем ``interface``.
"""
from typing import Any, Callable, Protocol

from ..domain.operations.plan import Action, Plan

# Порт чтения: вызвать фиксированный маршрут EPA и вернуть разобранный JSON.
# Отказ 1С приходит как ``GatewayError``.
CallApi = Callable[[str, dict[str, Any]], Any]


class OperationRepository(Protocol):
    """Долговременный журнал планов предпросмотра и операций записи."""

    def ready(self) -> bool: ...

    def create_scenario(self, state: dict[str, Any]) -> dict[str, Any]: ...

    def get_scenario(self, scenario_id: str) -> dict[str, Any]: ...

    def save_scenario(self, state: dict[str, Any], revision: int) -> dict[str, Any]: ...

    def create_plan(self, action: Action, payload: dict[str, Any], preview: Any, ttl_seconds: int) -> Plan: ...

    def get_plan(self, plan_id: str) -> Plan: ...

    def begin(self, plan_id: str, operation_id: str, expected_action: Action) -> tuple[Plan, Any | None]: ...

    def begin_direct(self, action: Action, payload: dict[str, Any], operation_id: str) -> tuple[Plan, Any | None]: ...

    def complete(self, operation_id: str, result: Any) -> None: ...

    def mark_indeterminate(self, operation_id: str, result: Any = None) -> None: ...

    def reject(self, operation_id: str, result: dict[str, Any]) -> None: ...

    def reconcile(self, operation_id: str, result: Any, *, rejected: bool = False) -> None: ...

    def operation_status(self, operation_id: str) -> dict[str, Any]: ...

    def relay_route(self, operation_id: str) -> str: ...

    def validate_relay_task(self, operation_id: str, route: str, task_id: str) -> None: ...


class DirectCapabilityCache(Protocol):
    """Кратковременная память о том, что выбранная база объявила прямые SQL-операции."""

    def has(self) -> bool: ...

    def remember(self, readiness: Any) -> None: ...

    def forget(self) -> None: ...


class AppContext(Protocol):
    """Окружение одного вызова: выбранная база, её журнал и маршрут до 1С."""

    @property
    def state_scope(self) -> str: ...

    @property
    def transport(self) -> str:
        """``direct_http`` или ``dcc`` для выбранной базы."""

    @property
    def plan_ttl_seconds(self) -> int: ...

    @property
    def operations(self) -> OperationRepository: ...

    @property
    def direct_capability(self) -> DirectCapabilityCache: ...

    def call(self, route: str, body: dict[str, Any], *, operation_id: str | None = None) -> Any:
        """Вызвать маршрут EPA. ``operation_id`` связывает запись с её операцией в журнале и DCC."""

    def read_snapshot(self, resource: str, snapshot_id: str | None = None, *,
                      params: dict[str, Any] | None = None,
                      body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Прочитать снимок метаданных DCC; для прямой базы — отказ без перехода на живое чтение."""

    def relay_operation(self, operation_id: str) -> dict[str, Any]:
        """Состояние операции в DCC по её исходному идентификатору."""
