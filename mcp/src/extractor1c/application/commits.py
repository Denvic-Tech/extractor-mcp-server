"""Запись в 1С: выполнение одобренного плана или прямой SQL-операции.

Каждая запись имеет ``operationId``. Журнал фиксирует полезную нагрузку до
обращения к 1С, поэтому повтор с тем же идентификатором только воспроизводит
результат, а неизвестный исход блокирует новые попытки до сверки.
"""
from typing import Any

from pydantic import TypeAdapter

from ..domain.decisions import confirm_saved
from ..domain.identifiers import is_uuid
from ..domain.epa.capabilities import CAPABILITY_REQUIRED, PublishedEpa
from ..domain.execution.outcomes import execution_result_problem
from ..domain.execution.requests import ProjectExportRef
from ..domain.operations.errors import OperationStoreError
from ..domain.operations.plan import Action
from ..domain.projects.changes import ProjectChange, ProjectRowsAppend, ProjectReceiverChange
from ..domain.projects.connections import bind_project_connection
from ..domain.projects.definition import GroupProjectDefinition, ProjectDefinition
from ..domain.projects.payloads import uses_column_names, uses_dotted_field_addition, uses_query_source
from .capabilities import require_capabilities
from .errors import ApplicationError, LicenseRejected
from .ports import AppContext
from .responses import project_response
from .state_tokens import PREFIX, resolve_state


def write_project(ctx: AppContext, action: Action, plan_id: str | None, operation_id: str, direct: Any) -> Any:
    """Select exactly one closed input contract, before journal or network work."""
    if (plan_id is None) == (direct is None):
        raise ApplicationError("Specify exactly one of plan_id or the direct operation input")
    if plan_id is not None:
        return commit(ctx, plan_id, operation_id, action)
    try:
        if action == "create":
            request = TypeAdapter(ProjectDefinition | GroupProjectDefinition).validate_python(direct)
        elif action == "update":
            request = TypeAdapter(ProjectChange).validate_python(direct)
            if isinstance(request, ProjectReceiverChange):
                raise ValueError("Receiver changes require preview_project and plan_id")
        else:
            request = ProjectExportRef(projectGuid=direct)
        # The caller cannot set this marker in definition/change. It is added
        # after strict validation, frozen in the journal, and checked against
        # the actual native receiver immediately before any write or start.
        payload = {**request.model_dump(exclude_none=True), "directSql": True}
        if isinstance(request, (GroupProjectDefinition, ProjectRowsAppend)):
            # EPA проверяет подключение всего запроса и каждой строки. Прямой
            # маршрут тоже связывает явный выбор до фиксации неизменяемого payload.
            name = request.connectionName or next(
                (row.connectionName for row in request.rows if row.connectionName), ""
            )
            if name:
                bind_project_connection(payload, name)
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None
    return commit(ctx, None, operation_id, action, direct_payload=payload)


def commit(ctx: AppContext, plan_id: str | None, operation_id: str, action: Action,
           *, direct_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Выполнить запись один раз и зафиксировать её исход в журнале."""
    try:
        store = ctx.operations
        plan, replay = (store.begin(plan_id, operation_id, action) if direct_payload is None
                        else store.begin_direct(action, direct_payload, operation_id))
    except OperationStoreError as exc:
        raise ApplicationError(str(exc)) from None
    if replay is not None:
        return {"planId": plan.plan_id, "operationId": operation_id,
                "replayed": True, "result": project_response(ctx, replay, plan.payload.get("projectName", ""))}
    if direct_payload is not None and _must_confirm_direct_capabilities(ctx, direct_payload):
        # Replay above is entirely local, including when 1C is offline. Only a
        # newly frozen request needs the native SQL guard capability. A failed
        # readiness read cannot have applied the later business operation.
        try:
            _confirm_direct_capabilities(ctx, direct_payload)
        except Exception:
            store.reject(operation_id, {"code": "DIRECT_SQL_CAPABILITY_UNAVAILABLE",
                         "error": "Native direct SQL capability could not be confirmed.", "writeApplied": False})
            raise
    if "receiverChange" in plan.payload:
        try:
            require_capabilities(ctx.call, "receiver_change")
        except Exception:
            store.reject(operation_id, {"code": "RECEIVER_CHANGE_UNAVAILABLE", "writeApplied": False})
            raise
    if uses_query_source(plan.payload):
        try:
            require_capabilities(ctx.call, "query_sources")
        except Exception:
            store.reject(operation_id, {"code": "QUERY_SOURCES_UNAVAILABLE",
                         "error": "Native query source capability could not be confirmed.", "writeApplied": False})
            raise
    try:
        wire_payload = (resolve_state(plan.payload, ctx.call, ctx.state_scope)
                        if str(plan.payload.get("expectedState", "")).startswith(PREFIX) else plan.payload)
    except ValueError as exc:
        store.reject(operation_id, {"code": "PROJECT_STATE_CONFLICT", "error": str(exc), "writeApplied": False})
        raise ApplicationError(str(exc)) from None
    except Exception:
        store.reject(operation_id, {"code": "STATE_VERIFICATION_UNAVAILABLE",
                     "error": "Could not read native state; no write performed", "writeApplied": False})
        raise
    try:
        result = ctx.execute_operation(action, wire_payload, operation_id)
    except LicenseRejected as exc:
        ctx.operations.reject(operation_id, exc.result)
        raise
    except Exception:
        ctx.operations.mark_indeterminate(operation_id)
        raise
    require_definite_outcome(ctx, action, plan.payload, operation_id, result)
    if (action in {"create", "update"} and isinstance(result, dict)
            and is_uuid(result.get("projectRef"))):
        try:
            saved = ctx.read_saved_project(plan.payload["projectName"])
            if not isinstance(saved, dict) or saved.get("projectRef") != result["projectRef"]:
                verification = {"status": "unavailable", "reason": "SAVED_PROJECT_IDENTITY_UNCONFIRMED"}
            else:
                verification = confirm_saved(plan.payload, saved, update_result=result if action == "update" else None)
                result = {**result, **saved}
        except (ApplicationError, OSError):
            verification = {"status": "unavailable", "reason": "READBACK_UNAVAILABLE",
                            "nextAction": "verify_project_summary"}
        except (ValueError, TypeError, KeyError):
            verification = {"status": "unavailable", "reason": "INVALID_SAVED_STATE",
                            "nextAction": "verify_project_summary"}
        result = {**result, "verification": verification}
    ctx.operations.complete(operation_id, result)
    return {"planId": plan.plan_id, "operationId": operation_id,
            "replayed": False, "result": project_response(ctx, result, plan.payload.get("projectName", ""))}


def require_definite_outcome(ctx: AppContext, action: str, payload: dict[str, Any],
                             operation_id: str, result: Any) -> None:
    """Ответ, который нельзя считать исходом, оставляет операцию неопределённой."""
    problem = execution_result_problem(action, payload, result)
    if problem is not None:
        ctx.operations.mark_indeterminate(operation_id, result)
        raise ApplicationError(problem)


def _must_confirm_direct_capabilities(ctx: AppContext, payload: dict[str, Any]) -> bool:
    return not ctx.direct_capability.has() or "fieldChanges" in payload or uses_column_names(payload)


def _confirm_direct_capabilities(ctx: AppContext, payload: dict[str, Any]) -> None:
    readiness = ctx.call("service/readiness", {})
    published = PublishedEpa.from_readiness(readiness)
    required = ["direct_sql_operations"]
    if "fieldChanges" in payload:
        required.append("field_mapping")
        if "rowIdentifier" in payload:
            required.append("field_selection")
        if uses_dotted_field_addition(payload):
            required.append("field_selection_paths")
    if uses_column_names(payload):
        required.append("column_names")
    missing = published.first_missing(*required)
    if missing is not None:
        raise ApplicationError(CAPABILITY_REQUIRED[missing])
    ctx.direct_capability.remember(readiness)
