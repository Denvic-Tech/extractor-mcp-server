"""Контекст приложения поверх среды выполнения сервера.

Каждое свойство обращается к ``runtime`` в момент использования: сценарий
получает базу, журнал и настройки текущего вызова, а не момента импорта.
"""
from typing import Any

from ...application.ports import AppContext, DirectCapabilityCache, OperationRepository
from ...domain.epa.routes import ACTION_ROUTES
from ...domain.operations.plan import Action
from . import runtime


class _DirectCapability:
    def has(self) -> bool:
        return runtime._has_direct_capability()

    def remember(self, readiness: Any) -> None:
        runtime._remember_direct_capability(readiness)

    def forget(self) -> None:
        runtime._forget_direct_capability()


class RuntimeContext:
    """Реализация ``AppContext`` для выбранной базы и клиента текущего запроса."""

    @property
    def state_scope(self) -> str:
        return _state_scope()

    @property
    def transport(self) -> str:
        return runtime._target.get().settings.transport

    @property
    def plan_ttl_seconds(self) -> int:
        return runtime.settings().plan_ttl_seconds

    @property
    def operations(self) -> OperationRepository:
        return runtime.operation_store()

    @property
    def direct_capability(self) -> DirectCapabilityCache:
        return _DirectCapability()

    def call(self, route: str, body: dict[str, Any], *, operation_id: str | None = None) -> Any:
        if operation_id is None:
            return runtime.call_api(route, body)
        token = runtime._operation_id.set(operation_id)
        try:
            return runtime.call_api(route, body)
        finally:
            runtime._operation_id.reset(token)

    def execute_operation(self, action: Action, payload: dict[str, Any], operation_id: str) -> Any:
        return self.call(ACTION_ROUTES[action], payload, operation_id=operation_id)

    def read_saved_project(self, project_name: str) -> Any:
        return self.call("projects/verify", {"projectName": project_name})

    def read_snapshot(self, resource: str, snapshot_id: str | None = None, *,
                      params: dict[str, Any] | None = None,
                      body: dict[str, Any] | None = None) -> dict[str, Any]:
        extra = {key: value for key, value in (("params", params), ("body", body)) if value is not None}
        return runtime._read_dcc_metadata(resource, snapshot_id, **extra)

    def relay_operation(self, operation_id: str) -> dict[str, Any]:
        return runtime.relay_operation(operation_id)


CONTEXT: AppContext = RuntimeContext()


def _state_scope() -> str:
    target = runtime._target.get()
    if target is not None:
        identity = target.identity
    else:
        settings = runtime.settings()
        identity = settings.url + "\0" + settings.user
    return identity + "\0" + runtime.authenticated_principal.get()
