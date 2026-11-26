"""Проверка ответа 1С на изменяющую операцию.

Операцию завершает только явный и корректно оформленный исход. Всё остальное
оставляет её неопределённой: повторять запись с новым ID нельзя.
"""
from typing import Any

from ..identifiers import is_uuid
from ..projects.payloads import matches_field_mapping_row
from .requests import ProjectExportCancelRef, ProjectExportStatus

FIELD_MAPPING_INDETERMINATE = "Field mapping update is indeterminate; verify the original operation and saved fields"
CANCELLATION_INDETERMINATE = "Cancellation is indeterminate; read the original task status before another request"
EXECUTION_INDETERMINATE = (
    "Project execution is indeterminate; read get_operation_status and task status before any new start"
)


def execution_result_problem(action: str, payload: dict[str, Any], result: Any) -> str | None:
    """Вернуть причину, по которой ответ нельзя считать исходом операции, или ``None``."""
    if action == "update" and "receiverChange" in payload:
        if (not isinstance(result, dict) or result.get("ok") is not True
                or not is_uuid(result.get("projectRef"))
                or result.get("projectName") != payload["projectName"]
                or result.get("currentTableName") != payload["currentTableName"]
                or result.get("table") != payload["receiverChange"]["tableName"]
                or result.get("tableExists") is not True
                or result.get("settingsPreserved") is not True):
            return "Receiver change is indeterminate; verify the original operation and receiver"
    if action == "update" and "fieldChanges" in payload:
        if (not isinstance(result, dict) or result.get("ok") is not True
                or result.get("projectName") != payload["projectName"]
                or not matches_field_mapping_row(payload, result)
                or result.get("requested") != payload["fieldChanges"]):
            return FIELD_MAPPING_INDETERMINATE
    if action == "cancel" and not _valid_cancellation(payload, result):
        return CANCELLATION_INDETERMINATE
    if action in {"export", "initialization"} and not _valid_launch(action, payload, result):
        return EXECUTION_INDETERMINATE
    return None


def _valid_cancellation(payload: dict[str, Any], result: Any) -> bool:
    valid = isinstance(result, dict) and result.get("cancelStatus") == "requested"
    valid = valid and str(result.get("taskId", "")).casefold() == payload["taskId"].casefold()
    valid = valid and str(result.get("projectGuid", "")).casefold() == payload["projectGuid"].casefold()
    if valid:
        try:
            ProjectExportCancelRef(projectGuid=payload["projectGuid"], taskId=result.get("backgroundJobId"))
        except ValueError:
            valid = False
    return valid


def _valid_launch(action: str, payload: dict[str, Any], result: Any) -> bool:
    # An acknowledged start is not a completed export.
    outcomes = {"started", "already_running", "initializing", "rejected"}
    if action == "initialization":
        outcomes.add("finished")  # Native fallback can execute synchronously.
    valid = isinstance(result, dict) and result.get("launchStatus") in outcomes
    if valid and result["launchStatus"] != "rejected":
        try:
            ProjectExportStatus(projectGuid=payload["projectGuid"], taskId=result.get("taskId"))
            valid = bool(result.get("taskId"))
            if result["launchStatus"] == "started":
                ProjectExportStatus(projectGuid=payload["projectGuid"], taskId=result.get("backgroundJobId"))
                valid = valid and bool(result.get("backgroundJobId"))
            elif result["launchStatus"] == "finished":
                valid = valid and result.get("status") in {"succeeded", "failed", "cancelled"}
        except ValueError:
            valid = False
    return valid
