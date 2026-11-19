"""Инструменты расписания проекта."""
from typing import Any

from ....application import schedules
from ....domain.schedules import ProjectSchedulePreview
from ..context import CONTEXT
from ..registry import READ, WRITE, base_tool, threaded_tool
from ._shared import _commit


@threaded_tool(annotations=READ)
@base_tool
def get_project_schedule(project_guid: str) -> Any:
    """Read the project's actual scheduled job, activity, time context and blockers."""
    return CONTEXT.call("projects/schedule/get", {"projectGuid": project_guid})


@threaded_tool(annotations=READ)
@base_tool
def preview_project_schedule(change: ProjectSchedulePreview) -> Any:
    """Preview daily/weekly/interval scheduling; omit schedule to pause/resume unchanged."""
    return schedules.preview_project_schedule(CONTEXT, change)


@threaded_tool(annotations=WRITE)
@base_tool
def set_project_schedule(plan_id: str, operation_id: str) -> Any:
    """Apply the approved schedule plan; enabling can start export. Read back afterwards."""
    return _commit(plan_id, operation_id, "schedule")
