"""Общие шаги нескольких инструментов: предпросмотр определения и запись по плану."""
from typing import Any

from ....application import commits, project_preview
from ....application.responses import preview_response
from ....domain.projects.changes import ProjectPreview
from ..context import CONTEXT
from ..registry import tool_errors


@tool_errors
def _preview_project_definition(definition: ProjectPreview, metadata_snapshot_id: str | None = None) -> Any:
    """Resolve receiver and store the exact approved native preview input once."""
    return preview_response(project_preview.preview_project_definition(CONTEXT, definition, metadata_snapshot_id))


@tool_errors
def _commit(plan_id: str | None, operation_id: str, action: str,
            *, direct_payload: dict[str, Any] | None = None) -> Any:
    return commits.commit(CONTEXT, plan_id, operation_id, action, direct_payload=direct_payload)


@tool_errors
def _write_project(action: str, plan_id: str | None, operation_id: str, direct: Any) -> Any:
    """Select exactly one closed input contract, before journal or network work."""
    return commits.write_project(CONTEXT, action, plan_id, operation_id, direct)
