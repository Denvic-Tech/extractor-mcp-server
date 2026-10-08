# //(Денис 2026-10-03. Версия MCP 1.2.0
# Общий анализ с account_scope и readiness с фактической версией опубликованных методов 1С.
"""Local MCP bridge to the fixed JSON web service in 1C."""
from __future__ import annotations

import argparse
import os
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from hashlib import sha256
import inspect
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import __version__
from .cloud_tools import (
    describe_object_page, preview_fields_page, preview_summary,
    search_metadata as search_metadata_data,
    verify_project_summary as verify_project_summary_data,
)
from .mcp_auth import BearerAuthMiddleware, authenticated_principal
from .transport import (resolve_target, call_target, list_bases as transport_list_bases,
                        get_target_operation, DccOperationPending, operation_result)
from .reference_graph import analyze_reference_graph as analyze_reference_graph_data
from .connections import resolve_project_connection, select_connection
from .operation_store import OperationRejected, OperationStore, OperationStoreError
from .skill_catalog import get_skill_data, list_skills_data
from .project_template import ProjectTemplateRequest, project_template_schema
from .projects_api import (
    ApiSettings, ProjectsApiClient, ProjectsApiError,
    ProjectDefinition, GroupProjectDefinition, ProjectPreview, ProjectRowChange, ProjectUpdate,
    ProjectSchedulePreview, ProjectScheduleSet, ProjectExportRef, ProjectExportStart, ProjectExportStatus,
)

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# FastMCP при host=127.0.0.1 сам включает localhost-only DNS-rebinding защиту
# (allowed_hosts=127.0.0.1/localhost), из-за чего опубликованный по внешнему хосту
# эндпоинт /mcp отвечает 421 Invalid Host header. Задаём защиту явно: по умолчанию
# выключена (наружу торчит только Caddy, контейнер — во внутренней сети), но её
# можно ужесточить, перечислив разрешённые Host в EXTRACTOR_PROJECTS_ALLOWED_HOSTS
# через запятую, напр. "mcp.example.test". Caddy пробрасывает исходный Host, так
# что при включении значение должно совпадать с доменом, иначе будет 421.
_allowed_hosts = [h.strip() for h in os.getenv("EXTRACTOR_PROJECTS_ALLOWED_HOSTS", "").split(",") if h.strip()]
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=bool(_allowed_hosts),
    allowed_hosts=_allowed_hosts,
    allowed_origins=[f"http://{h}" for h in _allowed_hosts] + [f"https://{h}" for h in _allowed_hosts],
)

AI_SKILL_REQUIREMENTS = (
    "AI requirement after authentication: before project work, you MUST call "
    "list_skills, then call get_skill with the returned skill_id and "
    'document="SKILL.md" for each relevant skill. Read the returned Markdown '
    "and use get_skill to read its task-relevant reference documents, using "
    "document names returned by list_skills. For project operations, read "
    'get_skill(skill_id="extractor1c", document="references/10-mcp-projects.md"). '
    "Do not treat the skill listing or tool descriptions as a substitute for "
    "reading the skill documents. If a required document cannot be read, "
    "report the failure and stop dependent project work."
)

SERVER_INSTRUCTIONS = AI_SKILL_REQUIREMENTS + """ Use search_metadata and describe_object_fields for discovery. Before project writes call preview_project and obtain planId. Ask the user to approve the preview, then call create_project or update_project with that planId and a new UUID operationId. For schedules read get_project_schedule, call preview_project_schedule, show the time context, execution user and activation warning, obtain user approval, then set_project_schedule with only planId and a new UUID operationId and read get_project_schedule again. Enabling a schedule can start data export. A repeated operationId only replays a completed result; an indeterminate operation must be resolved with verify_project_summary (or get_project_schedule for schedules) before any new write. Add related project rows one at a time and verify after each write. Project creation configures export settings but does not run data export. Never send arbitrary code or SQL."""

SERVER_INSTRUCTIONS += """ For immediate export call preview_project_export, show the whole project, current HTTP execution user and immediate activation, then after user authorization call start_project_export with only planId and a new UUID operationId. Poll get_project_export_status with the returned taskId. get_operation_status tracks the launch request, not export completion. Never retry an uncertain launch with another plan or operation ID: unresolved launches block further starts for that project in this operation store and require administrator reconciliation. Reading status alone does not clear this block. The latest task is not proof of which request started it."""

SERVER_INSTRUCTIONS += """ Queue initialization is separate from export: use preview_project_initialization, show eligible queue rows, handler counts, current HTTP user and effects, then initialize_project_queue with the approved planId and operationId. Native initialization handlers may clear or refill queues; existing schedules may consume prepared data. Do not initialize merely because export was requested. Poll get_project_export_status using the returned taskId (taskType initialization); without taskId it still reads latest export. A finished launch means native synchronous fallback ended: inspect its status, not just launchStatus. Uncertain initialization and export operations block both kinds of new launch for the same project."""

SERVER_INSTRUCTIONS += """ Call list_bases before base-specific work. Pass the same base_id to discovery, preview, commit and status tools. Direct HTTP bases come from the configured YAML bases_file or environment settings; DCC bases are all accessible connectors advertising extractor-1c/2.0 and the mcp command. There is no automatic transport fallback. If DCC waiting expires, query get_operation_status with the same operation_id and base_id; never submit another write to replace an uncertain request."""

mcp = FastMCP("extractor1c", instructions=SERVER_INSTRUCTIONS,
              host="127.0.0.1", port=8001,
              stateless_http=True, json_response=True,
              transport_security=_transport_security)
mcp._mcp_server.version = __version__
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)

_target = ContextVar("extractor_target", default=None)
_operation_id = ContextVar("extractor_operation_id", default=None)


def base_tool(function):
    """Expose base selection and bind all nested calls to one authorized target."""
    @wraps(function)
    def wrapped(*args, base_id: str | None = None, **kwargs):
        try:
            target = resolve_target(ApiSettings(_env_file=ENV_FILE), base_id)
        except (ValueError, ProjectsApiError, requests.RequestException):
            raise ToolError("Cannot select base; call list_bases and choose an accessible base_id") from None
        token = _target.set(target)
        try:
            return function(*args, **kwargs)
        finally:
            _target.reset(token)
    signature = inspect.signature(function, eval_str=True)
    wrapped.__signature__ = signature.replace(parameters=[*signature.parameters.values(),
        inspect.Parameter("base_id", inspect.Parameter.KEYWORD_ONLY, default=None,
                          annotation=str | None)])
    wrapped.__annotations__ = {**inspect.get_annotations(function, eval_str=True), "base_id": str | None}
    return wrapped


@lru_cache(maxsize=4)
def _operation_store(path: str) -> OperationStore:
    return OperationStore(path)


def operation_store() -> OperationStore:
    settings = ApiSettings(_env_file=ENV_FILE)
    target = _target.get()
    principal = authenticated_principal.get()
    if target is not None:
        digest = sha256((target.identity + "\0" + principal).encode()).hexdigest()
        path = Path(settings.operation_db)
        return _operation_store(str(path.with_name(path.stem + "-" + digest + path.suffix)))
    return _operation_store(settings.operation_db)


class LicenseRejectedToolError(ToolError):
    """Confirmed refusal by the 1C license gate, before any project/table write."""

    def __init__(self, result):
        self.result = result
        super().__init__(str(OperationRejected(result)))


def call_api(route: str, body: dict) -> Any:
    settings = ApiSettings(_env_file=ENV_FILE)
    try:
        target = _target.get()
        if target is not None:
            return call_target(target, route, body, operation_id=_operation_id.get())
        with requests.Session() as session:
            session.trust_env = False
            return ProjectsApiClient(settings, session).call(route, body)
    except DccOperationPending as exc:
        operation_store().remember_relay(exc.operation_id, route)
        raise ToolError(f"DCC operation pending: operation_id={exc.operation_id}; query get_operation_status on the same base_id.") from None
    except ProjectsApiError as exc:
        if exc.rejected_before_write:
            raise LicenseRejectedToolError(exc.rejection()) from None
        raise ToolError(f"1C API HTTP {exc.status}; check publication, permissions and credentials.") from None
    except requests.RequestException as exc:
        raise ToolError(f"1C connection failed ({type(exc).__name__}).") from None


@mcp.tool(annotations=READ)
def list_bases() -> list[dict]:
    """List configured direct bases or all DCC connectors accessible to this installation."""
    try:
        return transport_list_bases(ApiSettings(_env_file=ENV_FILE))
    except (ValueError, ProjectsApiError, requests.RequestException):
        raise ToolError("Cannot list bases; check transport configuration and access") from None


@mcp.tool(annotations=READ)
def health() -> dict:
    """Check local configuration; does not test the 1C connection."""
    settings = ApiSettings(_env_file=ENV_FILE)
    return {
        "configured": (bool(settings.dcc_url and settings.dcc_user and settings.dcc_password)
                       if settings.transport == "dcc" else bool(settings.bases or (settings.url and settings.user))),
        "transport": settings.transport,
        "tlsVerification": not settings.allow_http_dev,
        "mutualTls": bool(settings.client_cert and settings.client_key),
        "operationStoreConfigured": bool(settings.operation_db),
    }


@mcp.tool(annotations=READ)
@base_tool
def readiness() -> dict:
    """Verify that the MCP service can reach and authenticate to the 1C API."""
    store_ready = operation_store().ready()
    version_warning = None
    try:
        build = call_api("service/readiness", {})
    except ToolError as exc:
        if not str(exc).startswith(("1C API HTTP 400;", "1C API HTTP 404;")):
            raise
        build = {"methodsVersion": None, "capabilities": []}
        version_warning = "Published 1C API does not expose its methods version; update the extension"
    if not isinstance(build, dict) or (build.get("methodsVersion") is None and not version_warning) or (build.get("methodsVersion") is not None and not re.fullmatch(r"\d+\.\d+\.\d+", str(build["methodsVersion"]))):
        raise ToolError("1C API did not report its methods version; update the API extension")
    if build["methodsVersion"] is not None and build["methodsVersion"] != __version__:
        version_warning = (
            f"Published 1C methods {build['methodsVersion']} differ from MCP contract {__version__}; "
            "synchronize the EPA module before project writes"
        )
    connections = call_api("connections/list", {})
    if isinstance(connections, list):
        count = len(connections)
    elif isinstance(connections, dict) and isinstance(connections.get("items"), list):
        count = len(connections["items"])
    else:
        count = None
    try:
        selection = select_connection(connections)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    return {"ready": store_ready, "operationStore": store_ready,
            "oneCMethodsVersion": build["methodsVersion"], "oneCCapabilities": build.get("capabilities", []),
            "oneCMethodsVersionWarning": version_warning,
            "connectionCount": count, "connectionSelection": selection,
            "message": AI_SKILL_REQUIREMENTS}


@mcp.tool(annotations=READ)
def list_skills() -> dict:
    """List project skills from Markdown files. Call this to find skill IDs and documents to read with get_skill."""
    return list_skills_data()


@mcp.tool(annotations=READ)
def get_skill(skill_id: str, document: str = "SKILL.md") -> dict:
    """Read the full Markdown text of a project skill or one of its listed reference documents."""
    try:
        return get_skill_data(skill_id, document)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=READ)
@base_tool
def list_metadata() -> Any:
    """Read all 1C metadata objects. Prefer search_metadata for hosted clients."""
    return call_api("metadata/list", {})


@mcp.tool(annotations=READ)
@base_tool
def search_metadata(query: str = "", kinds: list[str] | None = None,
                    cursor: int = 0, limit: int = 50) -> Any:
    """Search and page metadata names without returning the complete metadata catalog."""
    try:
        return search_metadata_data(call_api, query, kinds, cursor, limit)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=READ)
@base_tool
def describe_object(object: str) -> Any:
    """Read every field of a 1C metadata object. Prefer describe_object_fields."""
    return call_api("metadata/describe", {"object": object})


@mcp.tool(annotations=READ)
@base_tool
def describe_object_fields(object: str, name_filter: str = "",
                           reference_only: bool = False, cursor: int = 0,
                           limit: int = 100) -> Any:
    """Read a compact, filtered page of object fields and exact metadata targets."""
    try:
        return describe_object_page(
            call_api, object, name_filter, reference_only, cursor, limit
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=READ)
@base_tool
def analyze_reference_graph(object: str, expand_paths: list[str] | None = None,
                            max_depth: int = 4, max_nodes: int = 500,
                            max_edges: int = 2000,
                            include_tabular_sections: bool = True,
                            account_scope: dict | None = None) -> Any:
    """Find outgoing references and dotted snowflake paths for any 1C entity.

    Expand selected paths and nonperiodic information registers. Skip periodic
    registers. Discover document tabular sections with the updated 1C API.
    Return exact polymorphic branches, join hints, cycles and explicit limits.
    """
    try:
        return analyze_reference_graph_data(
            call_api, object, expand_paths, max_depth, max_nodes, max_edges,
            include_tabular_sections,
            account_scope,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool(annotations=READ)
@base_tool
def list_connections() -> Any:
    """Read live connections of all types. None: ask to configure 1C. One: use it. Many: ask and reuse the user's choice in this conversation."""
    return call_api("connections/list", {})


@mcp.tool(annotations=READ)
@base_tool
def list_projects() -> Any:
    """Read projects visible through the 1C API."""
    return call_api("projects/list", {})


@mcp.tool(annotations=READ)
@base_tool
def verify_project(project_name: str) -> Any:
    """Read the complete project configuration and state token."""
    return call_api("projects/verify", {"projectName": project_name})


@mcp.tool(annotations=READ)
@base_tool
def verify_project_summary(project_name: str, table_name: str = "") -> Any:
    """Read a compact project summary without returning the large state token."""
    return verify_project_summary_data(call_api, project_name, table_name)


@mcp.tool(annotations=READ)
@base_tool
def preview_project(definition: ProjectPreview) -> Any:
    """Preview a create/row change and issue a short-lived planId when approved."""
    return _preview_project_definition(definition)


@mcp.tool(annotations=READ)
def get_project_template_schema() -> dict:
    """Return JSON Schema for template requests; does not access a base or create a plan."""
    return project_template_schema()


@mcp.tool(annotations=READ)
def validate_project_template(request: ProjectTemplateRequest) -> dict:
    """Validate JSON syntax/semantics only; metadata and receiver need target preview."""
    return {"valid": True, "schemaVersion": 1, "validationLevel": "request",
            "metadataChecked": False, "planId": None,
            "definition": request.native_definition().model_dump(),
            "nextStep": "preview_project_template"}


@mcp.tool(annotations=READ)
@base_tool
def preview_project_template(request: ProjectTemplateRequest) -> Any:
    """Validate in target 1C through configured transport, reusing ordinary create plans."""
    return _preview_project_definition(request.native_definition())


def _preview_project_definition(definition: ProjectPreview) -> Any:
    """Resolve receiver and store the exact approved native preview input once."""
    try:
        payload, selection = resolve_project_connection(definition.model_dump(exclude_none=True), call_api)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if selection["status"] != "selected":
        return {"approved": False, "planId": None, "expiresAt": None,
                "connectionSelection": selection}
    preview = call_api("projects/preview", payload)
    summary = preview_summary(preview, allow_existing_table=isinstance(definition, ProjectRowChange))
    action = "create" if isinstance(definition, (ProjectDefinition, GroupProjectDefinition)) else "update"
    summary["connectionName"] = selection["connectionName"]
    result = {"approved": summary["approved"], "action": action, "summary": summary,
              "connectionSelection": selection}
    if summary["approved"]:
        settings = ApiSettings(_env_file=ENV_FILE)
        plan = operation_store().create_plan(
            action, payload, preview, settings.plan_ttl_seconds
        )
        result.update({"planId": plan.plan_id, "expiresAt": plan.expires_at})
    else:
        result.update({"planId": None, "expiresAt": None})
    return result


@mcp.tool(annotations=READ)
@base_tool
def preview_project_properties(update: ProjectUpdate) -> Any:
    """Preview a project-property update and bind it to the verified state."""
    verified = call_api("projects/verify", {"projectName": update.projectName})
    state_matches = isinstance(verified, dict) and verified.get("stateToken") == update.expectedState
    preview = {
        "projectName": update.projectName,
        "stateMatches": state_matches,
        "changes": {
            "comment": update.comment,
            "disabled": update.disabled,
            "includeGuids": update.includeGuids,
            "includeMetadataTypes": update.includeMetadataTypes,
        },
    }
    if not state_matches:
        return {"approved": False, "action": "update", "planId": None,
                "expiresAt": None, "summary": preview}
    settings = ApiSettings(_env_file=ENV_FILE)
    plan = operation_store().create_plan(
        "update", update.model_dump(), preview, settings.plan_ttl_seconds
    )
    return {"approved": True, "action": "update", "planId": plan.plan_id,
            "expiresAt": plan.expires_at, "summary": preview}


@mcp.tool(annotations=READ)
@base_tool
def get_project_schedule(project_guid: str) -> Any:
    """Read the project's actual scheduled job, activity, time context and blockers."""
    return call_api("projects/schedule/get", {"projectGuid": project_guid})


@mcp.tool(annotations=READ)
@base_tool
def preview_project_schedule(change: ProjectSchedulePreview) -> Any:
    """Preview daily/weekly/interval scheduling; omit schedule to pause/resume unchanged."""
    payload = change.model_dump()
    preview = call_api("projects/schedule/preview", payload)
    if not isinstance(preview, dict):
        raise ToolError("Invalid schedule preview response; no plan was created")
    result = {"approved": False, "action": "schedule", "planId": None,
              "expiresAt": None, "summary": preview}
    if preview.get("approved") is not True:
        return result
    # Never allow the upstream preview to replace the user's requested settings.
    try:
        payload = ProjectScheduleSet(
            **payload, expectedScheduleState=preview.get("expectedScheduleState")
        ).model_dump()
    except ValueError:
        raise ToolError("Schedule preview did not supply a valid state token; no plan was created") from None
    settings = ApiSettings(_env_file=ENV_FILE)
    plan = operation_store().create_plan("schedule", payload, preview, settings.plan_ttl_seconds)
    result.update(approved=True, planId=plan.plan_id, expiresAt=plan.expires_at)
    return result


@mcp.tool(annotations=READ)
@base_tool
def preview_project_export(project_guid: str) -> Any:
    """Preview an immediate whole-project export; no export starts during preview."""
    return _preview_project_execution(project_guid, "export")


@mcp.tool(annotations=READ)
@base_tool
def preview_project_initialization(project_guid: str) -> Any:
    """Preview queue initialization: eligible rows, native handlers and immediate effects."""
    return _preview_project_execution(project_guid, "initialization")


def _preview_project_execution(project_guid: str, action: str) -> Any:
    payload = ProjectExportRef(projectGuid=project_guid).model_dump()
    preview = call_api(f"projects/{action}/preview", payload)
    if not isinstance(preview, dict):
        raise ToolError("Invalid execution preview response; no plan was created")
    result = {"approved": False, "action": action, "planId": None,
              "expiresAt": None, "summary": preview}
    if preview.get("approved") is not True:
        return result
    try:
        payload = ProjectExportStart(
            **payload, expectedProjectState=preview.get("expectedProjectState")
        ).model_dump()
    except ValueError:
        raise ToolError("Execution preview did not supply a valid state token; no plan was created") from None
    settings = ApiSettings(_env_file=ENV_FILE)
    plan = operation_store().create_plan(action, payload, preview, settings.plan_ttl_seconds)
    result.update(approved=True, planId=plan.plan_id, expiresAt=plan.expires_at)
    return result


@mcp.tool(annotations=READ)
@base_tool
def get_project_export_status(project_guid: str, task_id: str | None = None) -> Any:
    """Read an export/initialization task by taskId; without it read latest export. Never consumes messages."""
    request = ProjectExportStatus(projectGuid=project_guid, taskId=task_id)
    return call_api("projects/export/status", request.model_dump())


@mcp.tool(annotations=WRITE)
@base_tool
def start_project_export(plan_id: str, operation_id: str) -> Any:
    """Start the exact previewed project export now. Poll the returned taskId for its outcome."""
    return _commit(plan_id, operation_id, "export")


@mcp.tool(annotations=WRITE)
@base_tool
def initialize_project_queue(plan_id: str, operation_id: str) -> Any:
    """Run native queue initialization for the approved project; poll get_project_export_status by taskId."""
    return _commit(plan_id, operation_id, "initialization")


@mcp.tool(annotations=READ)
@base_tool
def get_project_plan(plan_id: str, cursor: int = 0, limit: int = 100) -> Any:
    """Read an approved plan and a paged view of its preview fields."""
    try:
        plan = operation_store().get_plan(plan_id)
        if plan.action in {"schedule", "export", "initialization"}:
            return {"planId": plan.plan_id, "action": plan.action,
                    "expiresAt": plan.expires_at, "summary": plan.preview}
        return {
            "planId": plan.plan_id,
            "action": plan.action,
            "expiresAt": plan.expires_at,
            "summary": preview_summary(plan.preview, allow_existing_table="currentTableName" in plan.payload),
            "fields": preview_fields_page(plan.preview, cursor, limit),
        }
    except (OperationStoreError, ValueError) as exc:
        raise ToolError(str(exc)) from None


def _commit(plan_id: str, operation_id: str, action: str) -> Any:
    try:
        plan, replay = operation_store().begin(plan_id, operation_id, action)
    except OperationStoreError as exc:
        raise ToolError(str(exc)) from None
    if replay is not None:
        return {"planId": plan.plan_id, "operationId": operation_id,
                "replayed": True, "result": replay}
    route = {"create": "projects/create", "update": "projects/update",
             "schedule": "projects/schedule/set", "export": "projects/export/start",
             "initialization": "projects/initialization/start"}[action]
    try:
        token = _operation_id.set(operation_id)
        try:
            result = call_api(route, plan.payload)
        finally:
            _operation_id.reset(token)
    except LicenseRejectedToolError as exc:
        operation_store().reject(operation_id, exc.result)
        raise
    except Exception:
        operation_store().mark_indeterminate(operation_id)
        raise
    _validate_execution_result(action, plan.payload, operation_id, result)
    operation_store().complete(operation_id, result)
    return {"planId": plan.plan_id, "operationId": operation_id,
            "replayed": False, "result": result}


def _validate_execution_result(action, payload, operation_id, result):
    if action in {"export", "initialization"}:
        # Only explicit, well-formed outcomes can complete the launch operation.
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
        if not valid:
            operation_store().mark_indeterminate(operation_id, result)
            raise ToolError("Project execution is indeterminate; read get_operation_status and task status before any new start")


@mcp.tool(annotations=WRITE)
@base_tool
def create_project(plan_id: str, operation_id: str) -> Any:
    """Create the exact approved plan; operationId makes completed calls replay-safe."""
    return _commit(plan_id, operation_id, "create")


@mcp.tool(annotations=WRITE)
@base_tool
def update_project(plan_id: str, operation_id: str) -> Any:
    """Apply the exact approved update plan; verify the project afterwards."""
    return _commit(plan_id, operation_id, "update")


@mcp.tool(annotations=WRITE)
@base_tool
def set_project_schedule(plan_id: str, operation_id: str) -> Any:
    """Apply the approved schedule plan; enabling can start export. Read back afterwards."""
    return _commit(plan_id, operation_id, "schedule")


@mcp.tool(annotations=READ)
@base_tool
def get_operation_status(operation_id: str) -> Any:
    """Read a durable write-operation status without retrying the write."""
    try:
        store = operation_store()
        target = _target.get()
        try:
            local = store.operation_status(operation_id)
        except OperationStoreError:
            if target.settings.transport != "dcc":
                raise
            route = store.relay_route(operation_id)
            state = get_target_operation(target, operation_id)
            ready, result = operation_result(state, route)
            return {"operationId": operation_id, "status": "completed" if ready else state["status"],
                    "result": result if ready else None}
        if target.settings.transport == "dcc" and local["status"] in {"in_progress", "indeterminate"}:
            state = get_target_operation(target, operation_id)
            plan = store.get_plan(local["planId"])
            route = {"create": "projects/create", "update": "projects/update",
                     "schedule": "projects/schedule/set", "export": "projects/export/start",
                     "initialization": "projects/initialization/start"}[local["action"]]
            try:
                ready, result = operation_result(state, route)
            except ProjectsApiError as exc:
                if not exc.rejected_before_write:
                    raise
                store.reconcile(operation_id, exc.rejection(), rejected=True)
            else:
                if ready:
                    _validate_execution_result(local["action"], plan.payload, operation_id, result)
                    store.reconcile(operation_id, result)
            local = store.operation_status(operation_id)
        return local
    except OperationStoreError as exc:
        raise ToolError(str(exc)) from None
    except (ProjectsApiError, requests.RequestException, ValueError):
        raise ToolError("Cannot read DCC operation; retain the same operation_id and base_id") from None


mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Extractor 1C MCP", version=__version__, lifespan=lifespan)
app.get("/health")(health)
app.mount("/", mcp_app)
# Bearer-token guard for every path except /health (used by the container probe).
# Tokens come from EXTRACTOR_PROJECTS_MCP_TOKENS; unset means fail-closed.
app.add_middleware(BearerAuthMiddleware)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="streamable-http")
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
# \\\Денис 2026-10-03. Версия MCP 1.2.0)
