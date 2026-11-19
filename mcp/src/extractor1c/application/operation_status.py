"""Состояние операции записи без повторения самой записи.

Для базы DCC неизвестный исход сверяется только с записью той же операции
в DCC: метод и задача должны совпасть с сохранёнными в журнале.
"""
from typing import Any

from ..domain.epa.errors import ProjectsApiError
from ..domain.epa.relay import operation_result
from ..domain.epa.routes import ACTION_ROUTES
from ..domain.operations.errors import OperationStoreError
from .commits import require_definite_outcome
from .errors import ApplicationError
from .ports import AppContext

_RELAY_FIELDS = ("operation_id", "task_id", "method", "status", "result_ready", "http_status",
                 "result", "diagnostic", "last_connector_contact", "completed_at", "expires_at")


def relay_details(state: dict[str, Any]) -> dict[str, Any]:
    """Expose operation diagnostics to its owner without copying the input payload."""
    return {key: state[key] for key in _RELAY_FIELDS if key in state}


def operation_status(ctx: AppContext, operation_id: str) -> dict[str, Any]:
    try:
        store = ctx.operations
        try:
            local = store.operation_status(operation_id)
        except OperationStoreError:
            if ctx.transport != "dcc":
                raise
            return _relay_only_status(ctx, operation_id)
        if ctx.transport == "dcc" and local["status"] in {"in_progress", "indeterminate"}:
            local = _reconcile_with_relay(ctx, operation_id, local)
        return local
    except OperationStoreError as exc:
        raise ApplicationError(str(exc)) from None
    except (ProjectsApiError, ValueError):
        raise ApplicationError("Cannot read DCC operation; retain the same operation_id and base_id") from None


def _matching_relay_state(ctx: AppContext, operation_id: str, route: str) -> dict[str, Any]:
    state = ctx.relay_operation(operation_id)
    if state.get("method") is not None and state["method"] != route:
        raise ProjectsApiError(502, "DCC operation method mismatch")
    ctx.operations.validate_relay_task(operation_id, route, state["task_id"])
    return state


def _relay_only_status(ctx: AppContext, operation_id: str) -> dict[str, Any]:
    """Чтение через DCC без записи в журнале операций: известен только маршрут и задача."""
    route = ctx.operations.relay_route(operation_id)
    state = _matching_relay_state(ctx, operation_id, route)
    if state.get("status") in {"failed", "cancelled", "recovery_required", "uncertain"}:
        return {"operationId": operation_id, "status": state["status"], "dcc": relay_details(state)}
    ready, result = operation_result(state, route)
    return {"operationId": operation_id, "status": "completed" if ready else state["status"],
            "result": result if ready else None}


def _reconcile_with_relay(ctx: AppContext, operation_id: str, local: dict[str, Any]) -> dict[str, Any]:
    """Разрешить незавершённую запись по её собственной записи в DCC."""
    store = ctx.operations
    state = ctx.relay_operation(operation_id)
    plan = store.get_plan(local["planId"])
    route = ACTION_ROUTES[local["action"]]
    if state.get("method") is not None and state["method"] != route:
        raise ProjectsApiError(502, "DCC operation method mismatch")
    store.validate_relay_task(operation_id, route, state["task_id"])
    if state.get("status") in {"cancelled", "recovery_required", "uncertain"}:
        return {**local, "dcc": relay_details(state)}
    try:
        ready, result = operation_result(state, route)
    except ProjectsApiError as exc:
        if not exc.rejected_before_write:
            if state.get("status") == "failed":
                # Keep the durable uncertainty block: an application error
                # alone does not prove that a side effect never occurred.
                return {**local, "dcc": relay_details(state)}
            raise
        store.reconcile(operation_id, exc.rejection(), rejected=True)
    else:
        if ready:
            require_definite_outcome(ctx, local["action"], plan.payload, operation_id, result)
            store.reconcile(operation_id, result)
    return {**store.operation_status(operation_id), "dcc": relay_details(state)}
