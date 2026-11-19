"""Compact agent responses; full state stays within server-side scenarios."""
from typing import Any

from ..domain.decisions import plan_decision
from ..domain.projects.summaries import state_from_verify, summarize_project
from .ports import AppContext
from .state_tokens import short_token

def project_response(ctx: AppContext, value: Any, project_name: str, *, details: bool = False,
                      table_name: str = "") -> Any:
    if not isinstance(value, dict):
        return value
    native = value.get("stateToken")
    full = "fields" in value or isinstance(value.get("rows"), list)
    result = (summarize_project(state_from_verify(value), project_name, table_name)
              if full and not details else dict(value))
    for key in ("ok", "stateMatches", "conflict", "changes", "error", "code", "verification", "rowIdentifier", "currentTableName"):
        if key in value:
            result[key] = value[key]
    if isinstance(native, str) and native:
        result["stateToken"] = short_token(native, ctx.state_scope, project_name)
    else:
        result.pop("stateToken", None)
    return result

def operation_response(ctx: AppContext, value: Any) -> Any:
    """Apply the same projection to durable results and DCC envelopes."""
    if isinstance(value, list):
        return [operation_response(ctx, item) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("projectName"), str) and (
            "fields" in value or "rows" in value or "stateToken" in value):
        return project_response(ctx, value, value["projectName"])
    return {key: operation_response(ctx, item) for key, item in value.items()}

def preview_response(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    selection = value.get("connectionSelection")
    if isinstance(selection, dict) and selection.get("status") != "selected":
        return {**value, "decision": {"status": "choice_required", "blockers": ["CONNECTION_SELECTION_REQUIRED"],
                                      "nextAction": "choose_connection"}}
    summary = value.get("summary", {})
    evidence = {**summary, "approved": value.get("approved")} if isinstance(summary, dict) else summary
    return {**value, "decision": plan_decision(evidence)}
