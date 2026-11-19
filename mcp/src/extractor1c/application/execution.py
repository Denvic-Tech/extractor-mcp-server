"""Выполнение проекта: предпросмотр запуска и отмены, состояние задачи, чтение приёмника.

Предпросмотр не запускает выгрузку и не меняет очередь. Чтение состояния не
потребляет сообщения и не снимает блокировку неопределённого запуска.
"""
import time
from typing import Any

from ..domain.decisions import task_decision, receiver_decision

from ..domain.epa.capabilities import CAPABILITY_REQUIRED, PublishedEpa
from ..domain.execution.requests import (
    ProjectExportCancel, ProjectExportCancelRef, ProjectExportRef, ProjectExportStart, ProjectExportStatus,
    ProjectQueryDiagnostics, ProjectReceiverInspect,
)
from .errors import ApplicationError, GatewayError
from .ports import AppContext

# Публикация без маршрута готовности отвечает 404 или 405: возможность не подтверждена.
_ROUTE_ABSENT = frozenset({404, 405})


def _published_or_refuse(ctx: AppContext, refusal: str) -> PublishedEpa:
    try:
        return PublishedEpa.from_readiness(ctx.call("service/readiness", {}))
    except GatewayError as exc:
        if exc.status in _ROUTE_ABSENT:
            raise ApplicationError(refusal) from None
        raise


def preview_execution(ctx: AppContext, project_guid: str, action: str) -> dict[str, Any]:
    """Предпросмотр немедленной выгрузки (``export``) или инициализации очереди (``initialization``)."""
    payload = ProjectExportRef(projectGuid=project_guid).model_dump()
    preview = ctx.call(f"projects/{action}/preview", payload)
    if not isinstance(preview, dict):
        raise ApplicationError("Invalid execution preview response; no plan was created")
    result = {"approved": False, "action": action, "planId": None,
              "expiresAt": None, "summary": preview}
    if preview.get("approved") is not True:
        return result
    try:
        payload = ProjectExportStart(
            **payload, expectedProjectState=preview.get("expectedProjectState")
        ).model_dump()
    except ValueError:
        raise ApplicationError("Execution preview did not supply a valid state token; no plan was created") from None
    plan = ctx.operations.create_plan(action, payload, preview, ctx.plan_ttl_seconds)
    result.update(approved=True, planId=plan.plan_id, expiresAt=plan.expires_at)
    return result


def export_status(ctx: AppContext, project_guid: str, task_id: str | None,
                  include_diagnostics: bool, details: bool = False, wait_seconds: int = 0) -> Any:
    """Read a task, optionally waiting within the bounded server interval."""
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 30:
        raise ApplicationError("wait_seconds must be an integer from 0 to 30")
    request = ProjectExportStatus(
        projectGuid=project_guid, taskId=task_id, includeDiagnostics=include_diagnostics,
    )
    if request.includeDiagnostics:
        published = _published_or_refuse(ctx, "Query diagnostics are unsupported; update published EPA methods")
        if not published.supports("query_diagnostics"):
            raise ApplicationError(CAPABILITY_REQUIRED["query_diagnostics"])
    wire = request.model_dump()
    if wait_seconds:
        wire.pop("includeDiagnostics", None)
    result = ctx.call("projects/export/status", wire)
    deadline = time.monotonic() + wait_seconds
    observed_task = result.get("taskId") if isinstance(result, dict) else None
    if wait_seconds and task_id and (not isinstance(observed_task, str) or observed_task.casefold() != task_id.casefold()):
        raise ApplicationError("Task identity changed while waiting; retain the original task_id")
    while (wait_seconds and observed_task and isinstance(result, dict)
           and result.get("status") in {"running", "queued", "initializing"}
           and time.monotonic() < deadline):
        time.sleep(min(1, max(0, deadline - time.monotonic())))
        pinned = ProjectExportStatus(projectGuid=project_guid, taskId=observed_task).model_dump()
        result = ctx.call("projects/export/status", pinned)
        if not isinstance(result, dict) or result.get("taskId") != observed_task:
            raise ApplicationError("Task identity changed while waiting; retain the original task_id")
    if wait_seconds and include_diagnostics:
        result = ctx.call("projects/export/status", ProjectExportStatus(
            projectGuid=project_guid, taskId=observed_task or task_id, includeDiagnostics=True).model_dump())
        if observed_task and (not isinstance(result, dict) or result.get("taskId") != observed_task):
            raise ApplicationError("Task identity changed while reading diagnostics; retain the original task_id")
    if request.includeDiagnostics:
        try:
            if not isinstance(result, dict):
                raise ValueError("Expected task status with diagnostics")
            ProjectQueryDiagnostics.model_validate(result.get("diagnostics"))
        except ValueError:
            raise ApplicationError("Invalid query diagnostics response from the selected EPA publication") from None
    return task_decision(result, details=details)


def preview_cancellation(ctx: AppContext, project_guid: str, task_id: str) -> dict[str, Any]:
    """Предпросмотр кооперативной остановки ровно этой задачи."""
    payload = ProjectExportCancelRef(projectGuid=project_guid, taskId=task_id).model_dump()
    preview = ctx.call("projects/export/cancel/preview", payload)
    if not isinstance(preview, dict):
        raise ApplicationError("Invalid cancellation preview; no plan was created")
    result = {"approved": False, "action": "cancel", "planId": None,
              "expiresAt": None, "summary": preview}
    if preview.get("approved") is not True:
        return result
    try:
        payload = ProjectExportCancel(
            **payload, expectedTaskState=preview.get("expectedTaskState")
        ).model_dump()
    except ValueError:
        raise ApplicationError("Cancellation preview supplied no valid task state; no plan was created") from None
    plan = ctx.operations.create_plan("cancel", payload, preview, ctx.plan_ttl_seconds)
    result.update(approved=True, planId=plan.plan_id, expiresAt=plan.expires_at)
    return result


def inspect_receiver(ctx: AppContext, request: ProjectReceiverInspect, details: bool = False) -> Any:
    """Схема, количество и явная ограниченная выборка приёмника сохранённой строки."""
    payload = request.model_dump()
    unsupported = "Receiver inspection requires published 1C methods 3.3.0+; update EPA"
    published = _published_or_refuse(ctx, unsupported)
    version = published.version_tuple()
    if version is None:
        raise ApplicationError("Cannot confirm receiver inspection support; update published EPA methods")
    if version[0] != 3 or version < (3, 3, 0):
        raise ApplicationError(unsupported)
    if not published.supports("receiver_inspection"):
        raise ApplicationError(CAPABILITY_REQUIRED["receiver_inspection"])
    # Readiness confirmed the feature. A receiver 404 can describe a missing
    # saved project/row; preserve the business code and diagnostic identifier.
    return receiver_decision(ctx.call("projects/receiver/inspect", payload), details=details)
