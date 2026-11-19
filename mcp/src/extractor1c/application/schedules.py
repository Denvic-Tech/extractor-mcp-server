"""Предпросмотр расписания проекта. Включение расписания может запустить выгрузку."""
from typing import Any

from ..domain.schedules import ProjectSchedulePreview, ProjectScheduleSet
from .errors import ApplicationError
from .ports import AppContext


def preview_project_schedule(ctx: AppContext, change: ProjectSchedulePreview) -> dict[str, Any]:
    payload = change.model_dump()
    preview = ctx.call("projects/schedule/preview", payload)
    if not isinstance(preview, dict):
        raise ApplicationError("Invalid schedule preview response; no plan was created")
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
        raise ApplicationError("Schedule preview did not supply a valid state token; no plan was created") from None
    plan = ctx.operations.create_plan("schedule", payload, preview, ctx.plan_ttl_seconds)
    result.update(approved=True, planId=plan.plan_id, expiresAt=plan.expires_at)
    return result
