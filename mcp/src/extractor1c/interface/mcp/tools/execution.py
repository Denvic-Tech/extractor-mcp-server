"""Инструменты выполнения: выгрузка, инициализация очереди, отмена и чтение приёмника."""
from typing import Annotated, Any

from pydantic import Field, StrictBool, StrictInt

from ....application import execution
from ....domain.execution.requests import ProjectReceiverInspect, ReceiverCheck
from ..context import CONTEXT
from ..registry import READ, WRITE, base_tool, threaded_tool
from ._shared import _commit, _write_project


@threaded_tool(annotations=READ)
@base_tool
def preview_project_export(project_guid: str) -> Any:
    """Preview an immediate whole-project export; no export starts during preview."""
    return execution.preview_execution(CONTEXT, project_guid, "export")


@threaded_tool(annotations=READ)
@base_tool
def preview_project_initialization(project_guid: str) -> Any:
    """Preview queue initialization: eligible rows, native handlers and immediate effects."""
    return execution.preview_execution(CONTEXT, project_guid, "initialization")


@threaded_tool(annotations=READ, forbid_unknown=True)
@base_tool
def get_project_export_status(
    project_guid: str, task_id: str | None = None, include_diagnostics: StrictBool = False,
    details: StrictBool = False, wait_seconds: Annotated[StrictInt, Field(ge=0, le=30)] = 0,
) -> Any:
    """Read task status; optionally include bounded, approximate native history.

    Diagnostics require task_id and the query_diagnostics EPA capability.
    History is not an exact task trace or typed parameter replay. Ordinary
    status remains compatible with older EPA and never consumes messages.
    """
    return execution.export_status(CONTEXT, project_guid, task_id, include_diagnostics, details, wait_seconds)


@threaded_tool(annotations=WRITE, forbid_unknown=True)
@base_tool
def start_project_export(plan_id: str | None = None, operation_id: str = Field(...), *,
                         project_guid: str | None = None) -> Any:
    """Start export by SQL project GUID or an approved plan; poll the returned taskId."""
    return _write_project("export", plan_id, operation_id, project_guid)


@threaded_tool(annotations=WRITE, forbid_unknown=True)
@base_tool
def initialize_project_queue(plan_id: str | None = None, operation_id: str = Field(...), *,
                             project_guid: str | None = None) -> Any:
    """Initialize a SQL project by GUID or use an approved plan; poll its taskId."""
    return _write_project("initialization", plan_id, operation_id, project_guid)


@threaded_tool(annotations=READ)
@base_tool
def preview_project_export_cancellation(project_guid: str, task_id: str) -> Any:
    """Preview cooperative cancellation of this exact task; never reset lost jobs."""
    return execution.preview_cancellation(CONTEXT, project_guid, task_id)


@threaded_tool(annotations=WRITE)
@base_tool
def cancel_project_export(plan_id: str, operation_id: str) -> Any:
    """Request stop for the approved task. Requested means acknowledged, not finished."""
    return _commit(plan_id, operation_id, "cancel")


@threaded_tool(annotations=READ, forbid_unknown=True)
@base_tool
def inspect_project_receiver(
    project_guid: str,
    project_row_id: str = "",
    checks: list[ReceiverCheck] | None = None,
    sample_fields: list[str] | None = None,
    sample_limit: StrictInt = 0,
    details: StrictBool = False,
) -> Any:
    """Read saved-row receiver schema/count/explicit bounded samples through EPA.

    Requires published 1C methods 3.3.0+. This never starts export, changes a
    project or runs Python SQL; receiver observation is not source equality.
    """
    request = ProjectReceiverInspect(
        projectGuid=project_guid, rowIdentifier=project_row_id,
        checks=["schema"] if checks is None else checks,
        sampleFields=[] if sample_fields is None else sample_fields,
        sampleLimit=sample_limit,
    )
    return execution.inspect_receiver(CONTEXT, request, details)
