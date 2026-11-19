"""Инструменты проектов: чтение, предпросмотр, создание и изменение."""
from typing import Any

from pydantic import Field

from ....application import project_preview, project_reading
from ....application import project_scenarios
from ....application.errors import ApplicationError
from ....application.responses import preview_response
from ....domain.projects.changes import ProjectChange, ProjectPreview, ProjectUpdate
from ....domain.projects.definition import GroupProjectDefinition, ProjectDefinition
from ....domain.projects.template import ProjectTemplateRequest, project_template_schema
from ....domain.projects.scenario import ScenarioInput
from ..context import CONTEXT
from .. import runtime
from ..registry import READ, WRITE, base_tool, threaded_tool
from ._shared import _preview_project_definition, _write_project


@threaded_tool(annotations=READ)
@base_tool
def list_connections() -> Any:
    """Read live connections of all types. None: ask to configure 1C. One: use it. Many: ask and reuse the user's choice in this conversation."""
    return CONTEXT.call("connections/list", {})


@threaded_tool(annotations=READ)
@base_tool
def list_projects() -> Any:
    """Read projects visible through the 1C API."""
    return CONTEXT.call("projects/list", {})


@threaded_tool(annotations=READ)
@base_tool
def verify_project(project_name: str, details: bool = False) -> Any:
    """Read a compact project summary and short state token; details includes all fields."""
    return project_reading.verify_project_summary(CONTEXT, project_name, details=details)


@threaded_tool(annotations=READ)
@base_tool
def verify_project_summary(project_name: str, table_name: str = "") -> Any:
    """Read a compact project summary with a short scoped state token."""
    return project_reading.verify_project_summary(CONTEXT, project_name, table_name)


@threaded_tool(annotations=READ)
@base_tool
def preview_project(definition: ProjectPreview | None = None, metadata_snapshot_id: str | None = None,
                    scenario: ScenarioInput | None = None) -> Any:
    """Preview a native plan, or save scenario choices and return the next existing tool call."""
    if scenario is not None:
        if metadata_snapshot_id is not None:
            raise ApplicationError("Scenario metadata is resolved in the selected base; do not override its snapshot")
        return _scenario_response(project_scenarios.scenario(CONTEXT, scenario, definition))
    if definition is None:
        raise ApplicationError("Specify a project definition or an existing scenario choice")
    return _preview_project_definition(definition, metadata_snapshot_id)


@threaded_tool(annotations=READ)
def get_project_template_schema() -> dict:
    """Return JSON Schema for template requests; does not access a base or create a plan."""
    return project_template_schema()


@threaded_tool(annotations=READ)
def validate_project_template(request: ProjectTemplateRequest) -> dict:
    """Validate JSON syntax/semantics only; metadata and receiver need target preview."""
    return {"valid": True, "schemaVersion": 1, "validationLevel": "request",
            "metadataChecked": False, "planId": None,
            "definition": request.native_definition().model_dump(),
            "nextStep": "preview_project_template"}


@threaded_tool(annotations=READ)
@base_tool
def preview_project_template(request: ProjectTemplateRequest, metadata_snapshot_id: str | None = None) -> Any:
    """Validate in target 1C through configured transport, reusing ordinary create plans."""
    return _preview_project_definition(request.native_definition(), metadata_snapshot_id)


@threaded_tool(annotations=READ)
@base_tool
def preview_project_properties(update: ProjectUpdate, scenario: ScenarioInput | None = None) -> Any:
    """Preview a project-property update and bind it to the verified state."""
    if scenario is not None:
        return _scenario_response(project_scenarios.scenario(CONTEXT, scenario, update))
    return preview_response(project_preview.preview_project_properties(CONTEXT, update))


@threaded_tool(annotations=READ)
@base_tool
def get_project_plan(plan_id: str, cursor: int = 0, limit: int = 100, details: bool = False) -> Any:
    """Read plan readiness and blockers; details=true includes paged preview fields."""
    if plan_id.startswith("scenario:"):
        return _scenario_response(project_scenarios.read_scenario(CONTEXT, plan_id))
    return project_reading.project_plan(CONTEXT, plan_id, cursor, limit, details)


def _scenario_response(value: dict[str, Any]) -> dict[str, Any]:
    target = runtime._target.get()
    if target is not None:
        for name in ("nextCall", "reply"):
            if name in value:
                value[name]["arguments"]["base_id"] = target.base_id
    return value


@threaded_tool(annotations=WRITE, forbid_unknown=True)
@base_tool
def create_project(plan_id: str | None = None, operation_id: str = Field(...), *,
                   definition: ProjectDefinition | GroupProjectDefinition | None = None) -> Any:
    """Create a SQL definition directly or an approved plan; operation_id is required."""
    return _write_project("create", plan_id, operation_id, definition)


@threaded_tool(annotations=WRITE, forbid_unknown=True)
@base_tool
def update_project(plan_id: str | None = None, operation_id: str = Field(...), *,
                   change: ProjectChange | None = None) -> Any:
    """Apply a SQL change with expectedState or an approved update plan."""
    return _write_project("update", plan_id, operation_id, change)
