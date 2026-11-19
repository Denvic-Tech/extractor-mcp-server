"""Closed scenario inputs. Native project contracts remain the source of truth."""
from pydantic import Field, StrictBool, StrictInt, model_validator

from ..requests import StrictRequest


class ScenarioInput(StrictRequest):
    scenarioId: str | None = Field(default=None, pattern=r"^scenario:[0-9a-f-]{36}$")
    revision: StrictInt | None = Field(default=None, ge=0)
    choice: str | list[str] | None = None
    initialize: StrictBool | None = None
    export: StrictBool | None = None
    wait: StrictBool | None = None

    @model_validator(mode="after")
    def require_revision(self):
        if self.scenarioId is not None and self.revision is None:
            raise ValueError("An existing scenario requires its exact revision")
        if self.scenarioId is None and self.choice is not None:
            raise ValueError("Start the scenario before selecting a returned choice")
        if self.scenarioId is not None and any(
                value is not None for value in (self.initialize, self.export, self.wait)):
            raise ValueError("Use the returned yes/no choices to change an existing scenario")
        return self


def failure_decision(code: str | None, *, uncertain: bool = False) -> dict:
    """Select an exact recovery lane; never infer success from free-form text."""
    if uncertain:
        return {"status": "reconciliation_required", "code": code or "OPERATION_UNCERTAIN",
                "nextAction": "get_operation_status", "retryAllowed": False}
    rules: dict[str, tuple[str, str]] = {
        "NO_ENABLED_QUEUE_ROWS": ("not_applicable", "choose_export_without_initialization"),
        "PROJECT_STATE_CONFLICT": ("blocked", "refresh_project_and_review_changes"),
        "STATE_VERIFICATION_UNAVAILABLE": ("blocked", "restore_server_verification"),
        "DIRECT_SQL_CAPABILITY_UNAVAILABLE": ("blocked", "update_server_capabilities"),
        "QUERY_SOURCES_UNAVAILABLE": ("blocked", "update_server_capabilities"),
        "LICENSE_REJECTED": ("blocked", "resolve_license"),
        "SAVED_SETTINGS_DIFFER": ("blocked", "review_saved_project_differences"),
        "SAVED_VERIFICATION_UNAVAILABLE": ("blocked", "restore_server_verification"),
        "TASK_STATUS_UNAVAILABLE": ("blocked", "read_same_task"),
        "CONNECTION_CONFIGURATION_REQUIRED": ("blocked", "configure_native_connection"),
        "PLAN_EXPIRED": ("blocked", "prepare_and_review_new_plan"),
        "PLAN_CONSUMED": ("blocked", "reconcile_original_operation"),
    }
    status, action = rules.get(code or "", ("blocked", "review_server_diagnostic"))
    return {"status": status, "code": code or "SERVER_DIAGNOSTIC_REQUIRED",
            "nextAction": action, "retryAllowed": False}
