"""Чтение сохранённого проекта и одобренного плана в компактном виде."""
from typing import Any

from ..domain.decisions import plan_decision
from ..domain.operations.errors import OperationStoreError
from ..domain.projects.summaries import preview_fields_page, preview_summary
from .errors import ApplicationError
from .ports import AppContext
from .responses import project_response

# Планы этих действий хранят ответ 1С как есть: у них нет строк и полей проекта.
_PLAIN_PREVIEW_ACTIONS = frozenset({"schedule", "export", "initialization", "cancel"})


def verify_project_summary(ctx: AppContext, project_name: str, table_name: str = "", *,
                           details: bool = False) -> dict[str, Any]:
    """Read one native state and return its scoped token and selected projection."""
    return project_response(ctx, ctx.call("projects/verify", {"projectName": project_name}),
                            project_name, details=details, table_name=table_name)


def project_plan(ctx: AppContext, plan_id: str, cursor: int = 0, limit: int = 100,
                 details: bool = False) -> dict[str, Any]:
    """Одобренный план и страница полей его предпросмотра."""
    try:
        plan = ctx.operations.get_plan(plan_id)
        if plan.operation_id:
            return {"planId": plan.plan_id, "action": plan.action, "operationId": plan.operation_id,
                    "summary": plan.preview if plan.is_direct else preview_summary(plan.preview),
                    "decision": {"status": "consumed", "blockers": ["PLAN_CONSUMED"],
                                 "nextAction": "get_operation_status"}}
        if plan.is_direct:
            return {"planId": plan.plan_id, "action": plan.action,
                    "summary": plan.preview, "operationId": plan.operation_id,
                    "decision": {"status": "operation_record", "nextAction": "get_operation_status"}}
        if plan.action in _PLAIN_PREVIEW_ACTIONS or "receiverChange" in plan.payload:
            return {"planId": plan.plan_id, "action": plan.action,
                    "expiresAt": plan.expires_at, "summary": plan.preview,
                    "decision": plan_decision(plan.preview)}
        result = {
            "planId": plan.plan_id,
            "action": plan.action,
            "expiresAt": plan.expires_at,
            "summary": preview_summary(
                plan.preview, allow_existing_table="currentTableName" in plan.payload,
                repair_missing_table="receiverRepair" in plan.payload,
            ),
        }
        if details:
            result["fields"] = preview_fields_page(plan.preview, cursor, limit)
        result["decision"] = plan_decision(result["summary"])
        if isinstance(plan.preview, dict) and "metadataEvidence" in plan.preview:
            result["metadataEvidence"] = plan.preview["metadataEvidence"]
        return result
    except (OperationStoreError, ValueError) as exc:
        raise ApplicationError(str(exc)) from None
