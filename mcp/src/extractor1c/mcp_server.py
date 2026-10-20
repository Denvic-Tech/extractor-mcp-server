# //(Денис 2026-10-03. Версия MCP 1.2.0
# Общий анализ с account_scope и readiness с фактической версией опубликованных методов 1С.
"""Local MCP bridge to the fixed JSON web service in 1C."""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from hashlib import sha256
import inspect
from pathlib import Path
from threading import Lock
from typing import Any, Callable, ParamSpec, TypeVar

import requests
from pydantic import Field, StrictBool, StrictInt, TypeAdapter
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import EPA_CONTRACT_VERSION, __version__
from .cloud_tools import (
    describe_object_page, preview_fields_page, preview_summary,
    search_metadata as search_metadata_data,
    verify_project_summary as verify_project_summary_data,
)
from .mcp_auth import BearerAuthMiddleware, authenticated_principal
from .transport import (resolve_target, call_target, list_bases as transport_list_bases,
                        get_target_operation, DccOperationPending, operation_result)
from .reference_graph import analyze_reference_graph as analyze_reference_graph_data
from .dcc_metadata import DccMetadataClient, MetadataResource
from .connections import bind_project_connection, resolve_project_connection, select_connection
from .operation_store import OperationRejected, OperationStore, OperationStoreError
from .skill_catalog import get_skill_data, list_skills_data
from .project_template import ProjectTemplateRequest, project_template_schema
from .projects_api import (
    ApiSettings, ProjectsApiClient, ProjectsApiError,
    ProjectDefinition, GroupProjectDefinition, ProjectChange, ProjectPreview, ProjectRowChange, ProjectUpdate,
    ProjectReceiverRepair, ProjectRowSettingsChange, ProjectRowsAppend, RowSettings, ProjectFieldMappingChange,
    ProjectSchedulePreview, ProjectScheduleSet, ProjectExportRef, ProjectExportStart, ProjectExportStatus,
    ProjectQueryDiagnostics,
    ProjectExportCancelRef, ProjectExportCancel,
    ProjectReceiverInspect, ReceiverCheck,
)

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# A capability read is an optimization, never authority to write. Native EPA
# still checks the actual receiver immediately before side effects, including
# after an extension downgrade. Keys contain only existing scoped identities;
# no credential, endpoint or response is persisted or exposed in this cache.
_direct_capabilities: OrderedDict[tuple[str, str], float] = OrderedDict()
_direct_capabilities_lock = Lock()
_DIRECT_CAPABILITY_TTL = 60.0


def _capability_key() -> tuple[str, str] | None:
    target = _target.get()
    return (target.identity, authenticated_principal.get()) if target is not None else None


def _forget_direct_capability() -> None:
    with _direct_capabilities_lock:
        _direct_capabilities.pop(_capability_key(), None)


def _remember_direct_capability(build: Any) -> None:
    capabilities = build.get("capabilities") if isinstance(build, dict) else None
    key = _capability_key()
    if key is None or not isinstance(capabilities, list) or "direct_sql_operations" not in capabilities:
        return
    with _direct_capabilities_lock:
        _direct_capabilities[key] = time.monotonic() + _DIRECT_CAPABILITY_TTL
        _direct_capabilities.move_to_end(key)
        while len(_direct_capabilities) > 256:
            _direct_capabilities.popitem(last=False)


def _has_direct_capability() -> bool:
    key = _capability_key()
    with _direct_capabilities_lock:
        until = _direct_capabilities.get(key)
        if until is None:
            return False
        if until <= time.monotonic():
            _direct_capabilities.pop(key)
            return False
        _direct_capabilities.move_to_end(key)
        return True

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

SERVER_INSTRUCTIONS = AI_SKILL_REQUIREMENTS + """ Use search_metadata and describe_object_fields for discovery. For SQL project writes use create_project(definition=...) or update_project(change=...) with a new UUID operation_id; direct mode requires the direct_sql_operations EPA capability and a native SQL receiver check. Updates retain expectedState. Preview is optional for SQL. For Kafka, AMQP and 1C Bus receivers preview_project is required: use its plan_id with operation_id. Do not ask for authorization again when the user already authorized the action. For schedules read get_project_schedule, call preview_project_schedule, show the time context, execution user and activation warning, obtain user approval, then set_project_schedule with only planId and a new UUID operationId and read get_project_schedule again. Enabling a schedule can start data export. A repeated operationId only replays a completed result; an indeterminate operation must be resolved with verify_project_summary (or get_project_schedule for schedules) before any new write. Add related project rows one at a time and verify after each write. Project creation configures export settings but does not run data export. Never send arbitrary code or SQL."""

SERVER_INSTRUCTIONS += """ For authorized immediate SQL export call start_project_export(project_guid=..., operation_id=...) directly; do not require a separate preview RPC. For Kafka, AMQP and 1C Bus use preview_project_export and then start_project_export(plan_id=..., operation_id=...). Native validation runs inside the direct SQL operation before launch. Poll get_project_export_status with the returned taskId. get_operation_status tracks the launch request, not export completion. Never retry an uncertain launch with another plan or operation ID: unresolved launches block further starts for that project in this operation store and require administrator reconciliation. Reading status alone does not clear this block. The latest task is not proof of which request started it."""

SERVER_INSTRUCTIONS += """ Queue initialization is separate from export. When explicitly authorized, initialize a SQL project directly with initialize_project_queue(project_guid=..., operation_id=...). For Kafka, AMQP and 1C Bus use preview_project_initialization and then initialize_project_queue(plan_id=..., operation_id=...). Never provide both a plan and direct input; operation_id is required in either mode. Native initialization handlers may clear or refill queues; existing schedules may consume prepared data. Do not initialize merely because export was requested. Poll get_project_export_status using the returned taskId (taskType initialization); without taskId it still reads latest export. A finished launch means native synchronous fallback ended: inspect its status, not just launchStatus. Uncertain initialization and export operations block both kinds of new launch for the same project."""

SERVER_INSTRUCTIONS += """ Call list_bases before base-specific work. Pass the same base_id to discovery, preview, commit and status tools. Direct HTTP bases come from the configured YAML bases_file or environment settings; DCC bases are all accessible connectors advertising extractor-1c/2.0 and the mcp command. There is no automatic transport fallback. If DCC waiting expires, query get_operation_status with the same operation_id and base_id; never submit another write to replace an uncertain request."""

SERVER_INSTRUCTIONS += """ For bounded native query/history diagnostics use get_project_export_status with the original task_id and include_diagnostics=true only when the selected EPA advertises query_diagnostics. This reads project history within a task time window; it is incomplete and cannot prove exact task correlation, typed parameters or replay. Keep native timestamp units and log text semantics. Ordinary status omits diagnostics and works with older EPA. Never replace unavailable diagnostics with arbitrary code or query execution."""

mcp = FastMCP("extractor1c", instructions=SERVER_INSTRUCTIONS,
              host="127.0.0.1", port=8001,
              stateless_http=True, json_response=True,
              transport_security=_transport_security)
mcp._mcp_server.version = __version__
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)

_target = ContextVar("extractor_target", default=None)
_operation_id = ContextVar("extractor_operation_id", default=None)

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result")


def threaded_tool(
    *, annotations: ToolAnnotations, forbid_unknown: bool = False,
) -> Callable[[Callable[_Parameters, _Result]], Callable[_Parameters, _Result]]:
    """Register blocking tools off the event loop; keep direct Python calls intact.

    DCC polling uses synchronous HTTP and sleep. ContextVars propagate into the
    worker, keeping the authenticated principal and selected base isolated.
    """
    def register(function: Callable[_Parameters, _Result]) -> Callable[_Parameters, _Result]:
        @wraps(function)
        async def execute(*args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _Result:
            return await asyncio.to_thread(function, *args, **kwargs)

        mcp.tool(annotations=annotations)(execute)
        if forbid_unknown:
            # FastMCP's default argument model ignores extras. Close only tools
            # opting into a strict boundary, before any backend call executes.
            registered = mcp._tool_manager.get_tool(function.__name__)
            assert registered is not None
            argument_model = registered.fn_metadata.arg_model
            argument_model.model_config = {**argument_model.model_config, "extra": "forbid"}
            argument_model.model_rebuild(force=True)
            registered.parameters = argument_model.model_json_schema()
        return function

    return register


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
    path = Path(settings.operation_db).expanduser()
    if not path.is_absolute():
        # Anchor state to the installation config, independent of an AI
        # client's working directory or the directory used to start Python.
        path = ENV_FILE.parent / path
    path = path.resolve()
    target = _target.get()
    principal = authenticated_principal.get()
    if target is not None:
        digest = sha256((target.identity + "\0" + principal).encode()).hexdigest()
        return _operation_store(str(path.with_name(path.stem + "-" + digest + path.suffix)))
    return _operation_store(str(path))


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
            on_accepted = operation_store().remember_relay if target.settings.transport == "dcc" else None
            return call_target(target, route, body, operation_id=_operation_id.get(), on_accepted=on_accepted)
        with requests.Session() as session:
            session.trust_env = False
            return ProjectsApiClient(settings, session).call(route, body)
    except DccOperationPending as exc:
        operation_store().remember_relay(exc.operation_id, route, exc.task_id)
        raise ToolError(f"DCC operation pending: operation_id={exc.operation_id}; query get_operation_status on the same base_id.") from None
    except ProjectsApiError as exc:
        if exc.rejected_before_write:
            raise LicenseRejectedToolError(exc.rejection()) from None
        details = f"1C API HTTP {exc.status}; {exc}"
        if exc.code:
            details += f"; code={exc.code}"
        if exc.error_id:
            details += f"; errorId={exc.error_id}"
        raise ToolError(details) from None
    except requests.RequestException as exc:
        raise ToolError(f"1C connection failed ({type(exc).__name__}).") from None


def _read_dcc_metadata(
    resource: MetadataResource,
    snapshot_id: str | None = None,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the selected DCC base's snapshot; errors never select another route."""
    target = _target.get()
    if target is None or target.settings.transport != "dcc":
        raise ToolError("Metadata snapshots require DCC transport")
    try:
        with DccMetadataClient(target.settings) as api:
            return api.read(target.base_id, resource, snapshot_id=snapshot_id, params=params, body=body)
    except (ValueError, ProjectsApiError) as exc:
        status = exc.status if isinstance(exc, ProjectsApiError) else 422
        raise ToolError(f"DCC metadata HTTP {status}; check snapshot and parameters; no transport fallback.") from None


@threaded_tool(annotations=READ)
def list_bases() -> list[dict]:
    """List configured direct bases or all DCC connectors accessible to this installation."""
    try:
        return transport_list_bases(ApiSettings(_env_file=ENV_FILE))
    except (ValueError, ProjectsApiError, requests.RequestException):
        raise ToolError("Cannot list bases; check transport configuration and access") from None


@threaded_tool(annotations=READ)
def health() -> dict:
    """Check local configuration; does not test the 1C connection."""
    settings = ApiSettings(_env_file=ENV_FILE)
    return {
        "configured": (bool(settings.dcc_url and settings.dcc_user and settings.dcc_password)
                       if settings.transport == "dcc" else bool(settings.bases or settings.bases_file or (settings.url and settings.user))),
        "transport": settings.transport,
        "tlsVerification": not settings.allow_http_dev,
        "mutualTls": bool(settings.client_cert and settings.client_key),
        "operationStoreConfigured": bool(settings.operation_db),
    }


@threaded_tool(annotations=READ)
@base_tool
def readiness() -> dict:
    """Verify that the MCP service can reach and authenticate to the 1C API."""
    # An explicit readiness call always refreshes the selected identity. A
    # missing capability or failed read must not leave an earlier positive hit.
    _forget_direct_capability()
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
    if build["methodsVersion"] is not None and build["methodsVersion"] != EPA_CONTRACT_VERSION:
        version_warning = (
            f"Published 1C methods {build['methodsVersion']} differ from MCP contract {EPA_CONTRACT_VERSION}; "
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
    _remember_direct_capability(build)
    return {"ready": store_ready, "operationStore": store_ready,
            "mcpVersion": __version__, "expectedOneCMethodsVersion": EPA_CONTRACT_VERSION,
            "oneCMethodsVersion": build["methodsVersion"], "oneCCapabilities": build.get("capabilities", []),
            "oneCMethodsVersionWarning": version_warning,
            "connectionCount": count, "connectionSelection": selection,
            "message": AI_SKILL_REQUIREMENTS}


@threaded_tool(annotations=READ)
def list_skills() -> dict:
    """List project skills from Markdown files. Call this to find skill IDs and documents to read with get_skill."""
    return list_skills_data()


@threaded_tool(annotations=READ)
def get_skill(skill_id: str, document: str = "SKILL.md") -> dict:
    """Read the full Markdown text of a project skill or one of its listed reference documents."""
    try:
        return get_skill_data(skill_id, document)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@threaded_tool(annotations=READ)
@base_tool
def list_metadata() -> Any:
    """Read all 1C metadata objects. Prefer search_metadata for hosted clients."""
    return call_api("metadata/list", {})


@threaded_tool(annotations=READ)
@base_tool
def get_metadata_snapshot(snapshot_id: str | None = None) -> dict[str, Any]:
    """Read DCC snapshot provenance; reuse snapshotId for paging and project plans."""
    return _read_dcc_metadata("snapshot", snapshot_id)


@threaded_tool(annotations=READ)
@base_tool
def search_metadata(query: str = "", kinds: list[str] | None = None,
                    cursor: int = 0, limit: int = 50,
                    snapshot_id: str | None = None) -> Any:
    """Search and page metadata names without returning the complete metadata catalog."""
    try:
        if _target.get().settings.transport == "dcc":
            result = _read_dcc_metadata("objects", snapshot_id,
                params={"query": query, "kinds": kinds or [], "cursor": cursor, "limit": limit})
            descriptions = result["items"]
            if any(not isinstance(item, dict) or not isinstance(item.get("fullName"), str) for item in descriptions):
                raise ToolError("Invalid DCC metadata object names")
            return {**result, "items": [item["fullName"] for item in descriptions],
                    "objectDescriptions": descriptions}
        if snapshot_id is not None:
            raise ValueError("snapshot_id requires DCC transport")
        return search_metadata_data(call_api, query, kinds, cursor, limit)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@threaded_tool(annotations=READ)
@base_tool
def describe_object(object: str) -> Any:
    """Read every field of a 1C metadata object. Prefer describe_object_fields."""
    return call_api("metadata/describe", {"object": object})


@threaded_tool(annotations=READ)
@base_tool
def describe_object_fields(object: str, name_filter: str = "",
                           reference_only: bool = False, cursor: int = 0,
                           limit: int = 100, snapshot_id: str | None = None) -> Any:
    """Read a compact, filtered page of object fields and exact metadata targets."""
    try:
        if _target.get().settings.transport == "dcc":
            return _read_dcc_metadata("fields", snapshot_id, params={"object": object,
                "name_filter": name_filter, "reference_only": reference_only, "cursor": cursor, "limit": limit})
        if snapshot_id is not None:
            raise ValueError("snapshot_id requires DCC transport")
        return describe_object_page(
            call_api, object, name_filter, reference_only, cursor, limit
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@threaded_tool(annotations=READ)
@base_tool
def analyze_reference_graph(object: str, expand_paths: list[str] | None = None,
                            max_depth: int = 4, max_nodes: int = 500,
                            max_edges: int = 2000,
                            include_tabular_sections: bool = True,
                            account_scope: dict | None = None,
                            snapshot_id: str | None = None) -> Any:
    """Find outgoing references and dotted snowflake paths for any 1C entity.

    Expand selected paths and nonperiodic information registers. Skip periodic
    registers. Discover document tabular sections with the updated 1C API.
    Return exact polymorphic branches, join hints, cycles and explicit limits.
    """
    try:
        if _target.get().settings.transport == "dcc" and account_scope is None:
            return _read_dcc_metadata("reference-graph", snapshot_id, body={"object": object,
                "expand_paths": expand_paths or [], "max_depth": max_depth, "max_nodes": max_nodes,
                "max_edges": max_edges, "include_tabular_sections": include_tabular_sections})
        if snapshot_id is not None:
            raise ValueError("snapshot_id requires DCC snapshot graph without live account_scope")
        return analyze_reference_graph_data(
            call_api, object, expand_paths, max_depth, max_nodes, max_edges,
            include_tabular_sections,
            account_scope,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from None


@threaded_tool(annotations=READ)
@base_tool
def list_connections() -> Any:
    """Read live connections of all types. None: ask to configure 1C. One: use it. Many: ask and reuse the user's choice in this conversation."""
    return call_api("connections/list", {})


@threaded_tool(annotations=READ)
@base_tool
def list_projects() -> Any:
    """Read projects visible through the 1C API."""
    return call_api("projects/list", {})


@threaded_tool(annotations=READ)
@base_tool
def verify_project(project_name: str) -> Any:
    """Read the complete project configuration and state token."""
    return call_api("projects/verify", {"projectName": project_name})


@threaded_tool(annotations=READ)
@base_tool
def verify_project_summary(project_name: str, table_name: str = "") -> Any:
    """Read a compact project summary without returning the large state token."""
    return verify_project_summary_data(call_api, project_name, table_name)


@threaded_tool(annotations=READ)
@base_tool
def preview_project(definition: ProjectPreview, metadata_snapshot_id: str | None = None) -> Any:
    """Preview a create/row change and issue a short-lived planId when approved."""
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


def _preview_project_definition(definition: ProjectPreview, metadata_snapshot_id: str | None = None) -> Any:
    """Resolve receiver and store the exact approved native preview input once."""
    raw_payload = definition.model_dump(exclude_none=True)
    if isinstance(definition, ProjectFieldMappingChange):
        # A saved-field patch is already bound to its receiver. Never rebuild
        # the helper definition or add connection overrides to this contract.
        _require_field_mapping_capability()
        preview = call_api("projects/preview", raw_payload)
        if (not isinstance(preview, dict)
                or preview.get("projectName") != definition.projectName
                or preview.get("currentTableName") != definition.currentTableName
                or preview.get("requested") != raw_payload["fieldChanges"]):
            raise ToolError("Invalid native field mapping preview; no plan was created")
        summary = preview_summary(preview)
        summary["approved"] = preview.get("stateMatches") is True and preview.get("conflict") is False
        if not summary["approved"]:
            return {"approved": False, "action": "update", "summary": summary, "planId": None}
        settings = ApiSettings(_env_file=ENV_FILE)
        plan = operation_store().create_plan("update", raw_payload, preview, settings.plan_ttl_seconds)
        return {"approved": True, "action": "update", "summary": summary,
                "planId": plan.plan_id, "expiresAt": plan.expires_at}
    rows = raw_payload.get("rows", [raw_payload.get("definition", raw_payload)])
    uses_row_settings = isinstance(definition, ProjectRowSettingsChange) or any(
        "rowSettings" in row for row in rows
    )
    if uses_row_settings:
        published = call_api("service/readiness", {})
        capabilities = published.get("capabilities") if isinstance(published, dict) else None
        if not isinstance(capabilities, list) or "row_settings" not in capabilities:
            raise ToolError("Native row settings require the row_settings EPA capability; update EPA")
    if isinstance(definition, ProjectRowSettingsChange):
        # This patch only changes saved row attributes. Do not discover or touch
        # a receiver, rebuild mappings, or inject connection overrides.
        preview = call_api("projects/preview", raw_payload)
        if not isinstance(preview, dict):
            raise ToolError("Invalid native row settings preview; no plan was created")
        summary = preview_summary(preview)
        if preview.get("stateMatches") is not True or preview.get("conflict") is not False:
            summary["approved"] = False
            return {"approved": False, "action": "update", "summary": summary,
                    "planId": None, "expiresAt": None}
        changes = preview.get("changes")
        change = changes.get("rowSettings", {}) if isinstance(changes, dict) else {}
        requested = raw_payload["rowSettings"]
        identity_valid = (
            preview.get("projectName") == definition.projectName
            and preview.get("currentTableName") == definition.currentTableName
            and isinstance(preview.get("projectRef"), str)
            and re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", preview["projectRef"])
            and isinstance(preview.get("rowIdentifier"), str) and bool(preview["rowIdentifier"].strip())
            and isinstance(preview.get("connectionName"), str)
        )
        try:
            echoed = RowSettings.model_validate(change["requested"]).model_dump()
            before = RowSettings.model_validate(change["before"]).model_dump()
            after = RowSettings.model_validate(change["after"]).model_dump()
            saved = RowSettings.model_validate(preview["rowSettings"]["saved"]).model_dump()
            settings_valid = (
                set(before) == set(RowSettings.model_fields)
                and set(after) == set(RowSettings.model_fields)
                and echoed == requested and saved == after
                and all(after[key] == value and type(after[key]) is type(value)
                        for key, value in requested.items())
                and all(after[key] == value for key, value in before.items() if key not in requested)
            )
        except (KeyError, TypeError, ValueError):
            settings_valid = False
        if not identity_valid or not settings_valid:
            raise ToolError("Invalid native row settings identity or changes; no plan was created")
        settings = ApiSettings(_env_file=ENV_FILE)
        plan = operation_store().create_plan("update", raw_payload, preview, settings.plan_ttl_seconds)
        return {"approved": True, "action": "update", "summary": summary,
                "connectionSelection": {"status": "selected", "connectionName": preview["connectionName"],
                                        "choices": [], "source": "saved_project"},
                "planId": plan.plan_id, "expiresAt": plan.expires_at}
    repair = isinstance(definition, ProjectReceiverRepair)
    if repair:
        published = call_api("service/readiness", {})
        capabilities = published.get("capabilities") if isinstance(published, dict) else None
        if not isinstance(capabilities, list) or "mysql_receiver_repair" not in capabilities:
            raise ToolError("MySQL receiver repair requires the mysql_receiver_repair EPA capability; update EPA")
    metadata_evidence = (_read_dcc_metadata("snapshot", metadata_snapshot_id)
                         if metadata_snapshot_id is not None else None)
    try:
        payload, selection = resolve_project_connection(raw_payload, call_api)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    if selection["status"] != "selected":
        return {"approved": False, "planId": None, "expiresAt": None,
                "connectionSelection": selection}
    if repair and not any(choice["name"] == selection["connectionName"]
                          and choice.get("type", "").casefold() == "mysql"
                          for choice in selection["choices"]):
        raise ToolError("Receiver repair requires the saved MySQL connection")
    preview = call_api("projects/preview", payload)
    if repair and (not isinstance(preview, dict)
                   or preview.get("receiverRepair") != definition.receiverRepair
                   or preview.get("textColumns") != definition.textColumns
                   or preview.get("table") != definition.currentTableName):
        raise ToolError("Invalid receiver repair preview; no plan was created")
    if uses_row_settings:
        preview_rows = preview.get("rows", [preview]) if isinstance(preview, dict) else []
        if not isinstance(preview_rows, list):
            raise ToolError("Invalid native row settings readback; no plan was created")
        for requested_row in rows:
            requested_settings = requested_row.get("rowSettings")
            if requested_settings is None:
                continue
            matching = [row for row in preview_rows if isinstance(row, dict)
                        and (row.get("table") or row.get("tableName")) == requested_row["tableName"]]
            try:
                if len(matching) != 1:
                    raise ValueError("Missing unique row settings preview")
                native_row = matching[0]
                saved = RowSettings.model_validate(native_row["rowSettings"]["saved"]).model_dump()
                if (set(saved) != set(RowSettings.model_fields)
                        or any(saved[key] != value or type(saved[key]) is not type(value)
                               for key, value in requested_settings.items())):
                    raise ValueError("Native row settings did not match the request")
                batch = native_row["sourceSettings"]["maxItemsPerBatch"]
                if ((batch is not None and type(batch) is not int)
                        or (0 if batch is None else batch) != saved["queueBatchSize"]):
                    raise ValueError("Native queue batch alias is inconsistent")
            except (KeyError, TypeError, ValueError):
                raise ToolError("Invalid native row settings readback; no plan was created") from None
    if metadata_evidence is not None:
        # Provenance belongs to the approved plan, never to the 1C business payload.
        preview = {**preview, "metadataEvidence": metadata_evidence}
    summary = preview_summary(
        preview, allow_existing_table=isinstance(definition, (ProjectRowChange, ProjectReceiverRepair)),
        repair_missing_table=repair,
    )
    action = "create" if isinstance(definition, (ProjectDefinition, GroupProjectDefinition)) else "update"
    summary["connectionName"] = selection["connectionName"]
    result = {"approved": summary["approved"], "action": action, "summary": summary,
              "connectionSelection": selection}
    if metadata_evidence is not None:
        result["metadataEvidence"] = metadata_evidence
    if summary["approved"]:
        settings = ApiSettings(_env_file=ENV_FILE)
        plan = operation_store().create_plan(
            action, payload, preview, settings.plan_ttl_seconds
        )
        result.update({"planId": plan.plan_id, "expiresAt": plan.expires_at})
    else:
        result.update({"planId": None, "expiresAt": None})
    return result


@threaded_tool(annotations=READ)
@base_tool
def preview_project_properties(update: ProjectUpdate) -> Any:
    """Preview a project-property update and bind it to the verified state."""
    verified = call_api("projects/verify", {"projectName": update.projectName})
    state_matches = isinstance(verified, dict) and verified.get("stateToken") == update.expectedState
    preview = {
        "projectName": update.projectName,
        "stateMatches": state_matches,
        "changes": {key: value for key, value in update.model_dump().items()
                    if key not in {"projectName", "expectedState"}},
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


@threaded_tool(annotations=READ)
@base_tool
def get_project_schedule(project_guid: str) -> Any:
    """Read the project's actual scheduled job, activity, time context and blockers."""
    return call_api("projects/schedule/get", {"projectGuid": project_guid})


@threaded_tool(annotations=READ)
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


@threaded_tool(annotations=READ)
@base_tool
def preview_project_export(project_guid: str) -> Any:
    """Preview an immediate whole-project export; no export starts during preview."""
    return _preview_project_execution(project_guid, "export")


@threaded_tool(annotations=READ)
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


@threaded_tool(annotations=READ, forbid_unknown=True)
@base_tool
def get_project_export_status(
    project_guid: str, task_id: str | None = None, include_diagnostics: StrictBool = False,
) -> Any:
    """Read task status; optionally include bounded, approximate native history.

    Diagnostics require task_id and the query_diagnostics EPA capability.
    History is not an exact task trace or typed parameter replay. Ordinary
    status remains compatible with older EPA and never consumes messages.
    """
    request = ProjectExportStatus(
        projectGuid=project_guid, taskId=task_id, includeDiagnostics=include_diagnostics,
    )
    if request.includeDiagnostics:
        try:
            published = call_api("service/readiness", {})
        except ToolError as exc:
            if re.search(r"1C API HTTP (?:404|405)\b", str(exc)):
                raise ToolError("Query diagnostics are unsupported; update published EPA methods") from None
            raise
        capabilities = published.get("capabilities") if isinstance(published, dict) else None
        if not isinstance(capabilities, list) or "query_diagnostics" not in capabilities:
            raise ToolError("Query diagnostics require the query_diagnostics EPA capability; update EPA")
    result = call_api("projects/export/status", request.model_dump())
    if request.includeDiagnostics:
        try:
            if not isinstance(result, dict):
                raise ValueError("Expected task status with diagnostics")
            ProjectQueryDiagnostics.model_validate(result.get("diagnostics"))
        except ValueError:
            raise ToolError("Invalid query diagnostics response from the selected EPA publication") from None
    return result


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
    payload = ProjectExportCancelRef(projectGuid=project_guid, taskId=task_id).model_dump()
    preview = call_api("projects/export/cancel/preview", payload)
    if not isinstance(preview, dict):
        raise ToolError("Invalid cancellation preview; no plan was created")
    result = {"approved": False, "action": "cancel", "planId": None,
              "expiresAt": None, "summary": preview}
    if preview.get("approved") is not True:
        return result
    try:
        payload = ProjectExportCancel(
            **payload, expectedTaskState=preview.get("expectedTaskState")
        ).model_dump()
    except ValueError:
        raise ToolError("Cancellation preview supplied no valid task state; no plan was created") from None
    plan = operation_store().create_plan(
        "cancel", payload, preview, ApiSettings(_env_file=ENV_FILE).plan_ttl_seconds
    )
    result.update(approved=True, planId=plan.plan_id, expiresAt=plan.expires_at)
    return result


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
) -> Any:
    """Read saved-row receiver schema/count/explicit bounded samples through EPA.

    Requires published 1C methods 3.3.0+. This never starts export, changes a
    project or runs Python SQL; receiver observation is not source equality.
    """
    payload = ProjectReceiverInspect(
        projectGuid=project_guid, rowIdentifier=project_row_id,
        checks=["schema"] if checks is None else checks,
        sampleFields=[] if sample_fields is None else sample_fields,
        sampleLimit=sample_limit,
    ).model_dump()
    try:
        published = call_api("service/readiness", {})
    except ToolError as exc:
        if re.search(r"1C API HTTP (?:404|405)\b", str(exc)):
            raise ToolError("Receiver inspection requires published 1C methods 3.3.0+; update EPA") from None
        raise
    version = published.get("methodsVersion") if isinstance(published, dict) else None
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ToolError("Cannot confirm receiver inspection support; update published EPA methods")
    major, minor, patch = map(int, version.split("."))
    if major != 3 or (major, minor, patch) < (3, 3, 0):
        raise ToolError("Receiver inspection requires published 1C methods 3.3.0+; update EPA")
    capabilities = published.get("capabilities")
    if not isinstance(capabilities, list) or "receiver_inspection" not in capabilities:
        raise ToolError("Receiver inspection is not advertised by the selected EPA publication; update EPA")
    # Readiness confirmed the feature. A receiver 404 can describe a missing
    # saved project/row; preserve the business code and diagnostic identifier.
    return call_api("projects/receiver/inspect", payload)


@threaded_tool(annotations=READ)
@base_tool
def get_project_plan(plan_id: str, cursor: int = 0, limit: int = 100) -> Any:
    """Read an approved plan and a paged view of its preview fields."""
    try:
        plan = operation_store().get_plan(plan_id)
        if isinstance(plan.preview, dict) and plan.preview.get("executionMode") == "direct_sql":
            return {"planId": plan.plan_id, "action": plan.action,
                    "summary": plan.preview, "operationId": plan.operation_id}
        if plan.action in {"schedule", "export", "initialization", "cancel"}:
            return {"planId": plan.plan_id, "action": plan.action,
                    "expiresAt": plan.expires_at, "summary": plan.preview}
        result = {
            "planId": plan.plan_id,
            "action": plan.action,
            "expiresAt": plan.expires_at,
            "summary": preview_summary(
                plan.preview, allow_existing_table="currentTableName" in plan.payload,
                repair_missing_table="receiverRepair" in plan.payload,
            ),
            "fields": preview_fields_page(plan.preview, cursor, limit),
        }
        if isinstance(plan.preview, dict) and "metadataEvidence" in plan.preview:
            result["metadataEvidence"] = plan.preview["metadataEvidence"]
        return result
    except (OperationStoreError, ValueError) as exc:
        raise ToolError(str(exc)) from None


def _commit(plan_id: str | None, operation_id: str, action: str,
            *, direct_payload: dict[str, Any] | None = None) -> Any:
    try:
        store = operation_store()
        plan, replay = (store.begin(plan_id, operation_id, action) if direct_payload is None
                        else store.begin_direct(action, direct_payload, operation_id))
    except OperationStoreError as exc:
        raise ToolError(str(exc)) from None
    if replay is not None:
        return {"planId": plan.plan_id, "operationId": operation_id,
                "replayed": True, "result": replay}
    if direct_payload is not None and (not _has_direct_capability() or "fieldChanges" in direct_payload):
        # Replay above is entirely local, including when 1C is offline. Only a
        # newly frozen request needs the native SQL guard capability. A failed
        # readiness read cannot have applied the later business operation.
        try:
            published = call_api("service/readiness", {})
            capabilities = published.get("capabilities") if isinstance(published, dict) else None
            if not isinstance(capabilities, list) or "direct_sql_operations" not in capabilities:
                raise ToolError("Direct SQL operations require the direct_sql_operations EPA capability; update EPA")
            if "fieldChanges" in direct_payload and "field_mapping" not in capabilities:
                raise ToolError("Field mapping changes require the field_mapping EPA capability; update EPA")
            _remember_direct_capability(published)
        except Exception:
            store.reject(operation_id, {"code": "DIRECT_SQL_CAPABILITY_UNAVAILABLE",
                         "error": "Native direct SQL capability could not be confirmed.", "writeApplied": False})
            raise
    route = {"create": "projects/create", "update": "projects/update",
             "schedule": "projects/schedule/set", "export": "projects/export/start",
             "initialization": "projects/initialization/start",
             "cancel": "projects/export/cancel"}[action]
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
    if action == "update" and "fieldChanges" in payload:
        if (not isinstance(result, dict) or result.get("ok") is not True
                or result.get("projectName") != payload["projectName"]
                or result.get("currentTableName") != payload["currentTableName"]
                or result.get("requested") != payload["fieldChanges"]):
            operation_store().mark_indeterminate(operation_id, result)
            raise ToolError("Field mapping update is indeterminate; verify the original operation and saved fields")
    if action == "cancel":
        valid = isinstance(result, dict) and result.get("cancelStatus") == "requested"
        valid = valid and str(result.get("taskId", "")).casefold() == payload["taskId"].casefold()
        valid = valid and str(result.get("projectGuid", "")).casefold() == payload["projectGuid"].casefold()
        if valid:
            try:
                ProjectExportCancelRef(projectGuid=payload["projectGuid"], taskId=result.get("backgroundJobId"))
            except ValueError:
                valid = False
        if not valid:
            operation_store().mark_indeterminate(operation_id, result)
            raise ToolError("Cancellation is indeterminate; read the original task status before another request")
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


def _require_field_mapping_capability() -> None:
    published = call_api("service/readiness", {})
    capabilities = published.get("capabilities") if isinstance(published, dict) else None
    if not isinstance(capabilities, list) or "field_mapping" not in capabilities:
        raise ToolError("Field mapping changes require the field_mapping EPA capability; update EPA")


def _write_project(action: str, plan_id: str | None, operation_id: str, direct: Any) -> Any:
    """Select exactly one closed input contract, before journal or network work."""
    if (plan_id is None) == (direct is None):
        raise ToolError("Specify exactly one of plan_id or the direct operation input")
    if plan_id is not None:
        return _commit(plan_id, operation_id, action)
    try:
        if action == "create":
            request = TypeAdapter(ProjectDefinition | GroupProjectDefinition).validate_python(direct)
        elif action == "update":
            request = TypeAdapter(ProjectChange).validate_python(direct)
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
        raise ToolError(str(exc)) from None
    return _commit(None, operation_id, action, direct_payload=payload)


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


@threaded_tool(annotations=WRITE)
@base_tool
def set_project_schedule(plan_id: str, operation_id: str) -> Any:
    """Apply the approved schedule plan; enabling can start export. Read back afterwards."""
    return _commit(plan_id, operation_id, "schedule")


@threaded_tool(annotations=READ)
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
            if state.get("method") is not None and state["method"] != route:
                raise ProjectsApiError(502, "DCC operation method mismatch")
            store.validate_relay_task(operation_id, route, state["task_id"])
            if state.get("status") in {"failed", "cancelled", "recovery_required", "uncertain"}:
                return {"operationId": operation_id, "status": state["status"], "dcc": _dcc_state_details(state)}
            ready, result = operation_result(state, route)
            return {"operationId": operation_id, "status": "completed" if ready else state["status"],
                    "result": result if ready else None}
        if target.settings.transport == "dcc" and local["status"] in {"in_progress", "indeterminate"}:
            state = get_target_operation(target, operation_id)
            plan = store.get_plan(local["planId"])
            route = {"create": "projects/create", "update": "projects/update",
                     "schedule": "projects/schedule/set", "export": "projects/export/start",
                     "initialization": "projects/initialization/start"}[local["action"]]
            if state.get("method") is not None and state["method"] != route:
                raise ProjectsApiError(502, "DCC operation method mismatch")
            store.validate_relay_task(operation_id, route, state["task_id"])
            if state.get("status") in {"cancelled", "recovery_required", "uncertain"}:
                return {**local, "dcc": _dcc_state_details(state)}
            try:
                ready, result = operation_result(state, route)
            except ProjectsApiError as exc:
                if not exc.rejected_before_write:
                    if state.get("status") == "failed":
                        # Keep the durable uncertainty block: an application error
                        # alone does not prove that a side effect never occurred.
                        return {**local, "dcc": _dcc_state_details(state)}
                    raise
                store.reconcile(operation_id, exc.rejection(), rejected=True)
            else:
                if ready:
                    _validate_execution_result(local["action"], plan.payload, operation_id, result)
                    store.reconcile(operation_id, result)
            local = {**store.operation_status(operation_id), "dcc": _dcc_state_details(state)}
        return local
    except OperationStoreError as exc:
        raise ToolError(str(exc)) from None
    except (ProjectsApiError, requests.RequestException, ValueError):
        raise ToolError("Cannot read DCC operation; retain the same operation_id and base_id") from None


def _dcc_state_details(state: dict[str, Any]) -> dict[str, Any]:
    """Expose operation diagnostics to its owner without copying the input payload."""
    fields = ("operation_id", "task_id", "method", "status", "result_ready", "http_status",
              "result", "diagnostic", "last_connector_contact", "completed_at", "expires_at")
    return {key: state[key] for key in fields if key in state}


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
