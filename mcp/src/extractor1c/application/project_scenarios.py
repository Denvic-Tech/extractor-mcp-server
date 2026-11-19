"""Durable project dialogue, using existing EPA previews and write operations.

The scenario never performs a business write. It returns the exact existing
write call after the choices have been made; its operation ID is persisted once.
"""
from copy import deepcopy
from typing import Any
from uuid import uuid4

from ..domain.decisions import field_choices
from ..domain.identifiers import is_uuid
from ..domain.operations.errors import OperationStoreError, PlanExpired, ScenarioConflict
from ..domain.projects.changes import ProjectPreview, ProjectUpdate
from ..domain.projects.definition import GroupProjectDefinition, ProjectDefinition
from ..domain.projects.connections import bind_project_connection
from ..domain.projects.scenario import ScenarioInput, failure_decision
from ..domain.projects.summaries import preview_fields_page
from . import execution, metadata, operation_status, project_preview
from .errors import ApplicationError, GatewayError
from .ports import AppContext
from .responses import preview_response


def scenario(ctx: AppContext, request: ScenarioInput,
             definition: ProjectPreview | ProjectUpdate | None = None) -> dict[str, Any]:
    try:
        if request.scenarioId is None:
            if definition is None:
                raise ApplicationError("Start a scenario with a typed project definition")
            payload = definition.model_dump(exclude_none=True)
            basic = isinstance(definition, ProjectDefinition) and bool(payload.get("object"))
            creating = isinstance(definition, (ProjectDefinition, GroupProjectDefinition))
            state = ctx.operations.create_scenario({
                "payload": payload, "stage": "prepare",
                "selectSegments": basic and definition.segmentation is None,
                "selectFields": basic and "selectedFields" not in definition.model_fields_set,
                "initialize": request.initialize if creating or request.initialize is not None else False,
                "export": request.export if creating or request.export is not None else False,
                "wait": request.wait,
            })
        else:
            if definition is not None:
                raise ApplicationError("An existing scenario accepts only a returned choice")
            state = ctx.operations.get_scenario(request.scenarioId)
            if request.revision != state["revision"]:
                return _conflict(ctx, request.scenarioId)
            if request.choice is None:
                return read_scenario(ctx, request.scenarioId)
            _select(state, request.choice)
            state = ctx.operations.save_scenario(state, request.revision)
        return _run(ctx, state)
    except ScenarioConflict:
        return _conflict(ctx, request.scenarioId)
    except OperationStoreError as exc:
        raise ApplicationError(str(exc)) from None


def read_scenario(ctx: AppContext, scenario_id: str) -> dict[str, Any]:
    try:
        return _run(ctx, ctx.operations.get_scenario(scenario_id))
    except ScenarioConflict:
        return _conflict(ctx, scenario_id)
    except OperationStoreError as exc:
        raise ApplicationError(str(exc)) from None


def _conflict(ctx: AppContext, scenario_id: str) -> dict[str, Any]:
    latest = _view(ctx.operations.get_scenario(scenario_id))
    latest["decision"] = {"status": "conflict", "code": "SCENARIO_REVISION_CONFLICT",
                          "nextAction": "get_project_plan", "retryAllowed": False}
    latest["nextCall"] = {"tool": "get_project_plan", "arguments": {"plan_id": scenario_id}}
    return latest


def _ask(state: dict, stage: str, question: str, choices: list[dict],
         *, multiple: bool = False) -> None:
    state.update(stage=stage, question=question, options=choices, multiple=multiple)


def _select(state: dict, selected: str | list[str]) -> None:
    values = selected if isinstance(selected, list) else [selected]
    options = {choice["id"]: choice for choice in state.get("options", [])}
    if (not values or len(values) != len(set(values)) or any(value not in options for value in values)
            or len(values) > 1 and not state.get("multiple")):
        raise ApplicationError("Select only the returned compatible choices for this scenario revision")
    stage = state["stage"]
    if stage == "connection":
        state["payload"]["connectionName"] = options[values[0]]["value"]
        state["stage"] = "prepare"
    elif stage == "segments":
        choice = options[values[0]]
        if "value" in choice:
            state["payload"].update(choice["value"])
        state.update(selectSegments=False, stage="prepare")
    elif stage == "fields":
        if "all_fields" in values and len(values) != 1:
            raise ApplicationError("Choose all fields or individual fields")
        state["payload"]["selectedFields"] = (
            [] if values == ["all_fields"] else list(dict.fromkeys(
                state.get("requiredFields", []) + [options[value]["value"] for value in values])))
        state.update(selectFields=False, stage="prepare")
    elif stage == "approve":
        state["stage"] = "write" if values == ["approve"] else "cancelled"
    elif stage in {"initialize_choice", "export_choice", "wait_choice"}:
        key = stage.removesuffix("_choice")
        state[key] = values == ["yes"]
        state["stage"] = "after_write"
    elif stage == "initialization_not_applicable":
        state.update(initialize=False, initializationDone=True, stage="after_write")
    elif stage == "expired_plan":
        if values == ["cancel"]:
            state["stage"] = "cancelled"
        else:
            action = state.pop("expiredAction", "write")
            state.pop("nextCall", None)
            if action == "write":
                state["stage"] = "prepare"
            else:
                state.pop(action + "Plan", None)
                state["stage"] = "after_write"
    else:
        raise ApplicationError("This scenario is not awaiting a choice")
    state.pop("options", None)
    state.pop("question", None)


def _run(ctx: AppContext, state: dict[str, Any]) -> dict[str, Any]:
    original = deepcopy(state)
    try:
        # One read can complete local transitions; it never loops on a native task.
        for _ in range(16):
            stage = state["stage"]
            if stage == "prepare":
                _prepare(ctx, state)
            elif stage == "write":
                if not _operation(ctx, state, "write", "create_project" if state["action"] == "create"
                                  else "update_project", state["planId"]):
                    break
                result = state["lastResult"]
                if not isinstance(result, dict) or not is_uuid(result.get("projectRef")):
                    state.update(stage="blocked", decision=failure_decision("SAVED_PROJECT_IDENTITY_UNCONFIRMED"))
                    break
                verification = result.get("verification", {}).get("status")
                if verification not in {"matched", "partial"}:
                    code = "SAVED_SETTINGS_DIFFER" if verification == "mismatch" else "SAVED_VERIFICATION_UNAVAILABLE"
                    state.update(stage="blocked", decision=failure_decision(code))
                    break
                state.update(projectGuid=result["projectRef"], stage="after_write")
            elif stage == "after_write":
                if state["initialize"] is None:
                    _yes_no(state, "initialize_choice", "Инициализировать очередь выгрузки?")
                elif state["initialize"] and not state.get("initializationDone"):
                    if not _launch(ctx, state, "initialization"):
                        break
                elif state["export"] is None:
                    _yes_no(state, "export_choice", "Выполнить выгрузку проекта сейчас?")
                elif state["export"] and state["wait"] is None:
                    _yes_no(state, "wait_choice", "Дождаться завершения выгрузки?")
                elif state["export"]:
                    if not _launch(ctx, state, "export"):
                        break
                else:
                    state["stage"] = "complete"
            else:
                break
    except (ApplicationError, ValueError, OperationStoreError) as exc:
        state.update(stage="blocked", decision=failure_decision(
            "PLAN_EXPIRED" if isinstance(exc, PlanExpired)
            else exc.code if isinstance(exc, GatewayError) else None))
        state["diagnostic"] = str(exc)[:1000]
        if isinstance(exc, PlanExpired):
            _ask(state, "expired_plan", "Срок плана истек. Подготовить новый план для сверки?", [
                {"id": "refresh_plan", "label": "Подготовить новый план"},
                {"id": "cancel", "label": "Отменить сценарий"}])
    if state != original:
        state = ctx.operations.save_scenario(state, original["revision"])
    return _view(state)


def _prepare(ctx: AppContext, state: dict) -> None:
    from pydantic import TypeAdapter
    definition = TypeAdapter(ProjectPreview | ProjectUpdate).validate_python(state["payload"])
    if state["selectSegments"]:
        # Metadata determines candidates; only EPA approves the resulting mapping.
        try:
            page = metadata.describe_fields(ctx, state["payload"]["object"], "", False, 0, 100, None)
            candidates = field_choices(page, state["payload"]["object"])
        except GatewayError as exc:
            if ctx.transport != "dcc" or exc.status != 404:
                raise
            # An absent optional snapshot page does not prevent the native
            # helper from generating its own mapping on the SAME DCC route.
            # Offer an explicit choice; never invent metadata or switch routes.
            candidates = {"segmentChoices": [], "choicesComplete": False}
            state["metadataDecision"] = {"code": "DCC_METADATA_UNAVAILABLE",
                                         "nextAction": "choose_native_helper_settings"}
        choices = [{"id": "native_defaults", "label": "Штатные настройки Помощника"},
                   {"id": "no_segments", "label": "Без сегментации", "value": {"segmentation": []}}]
        for field in candidates["segmentChoices"]:
            for segment in field["segments"]:
                grouping = {"field": field["field"], "segment": segment,
                            "regular": True, "extended": False, "initialization": True}
                batch = field["regularValueBatchSize"] if segment == "По значению" else 0
                choices.append({"id": "segment:" + str(len(choices)),
                                "label": field["field"] + ": " + segment,
                                "value": {"segmentation": [grouping], "maxItemsPerBatch": batch}})
        state["choicesComplete"] = candidates["choicesComplete"]
        _ask(state, "segments", "Выберите сегмент для регулярной выгрузки и инициализации.", choices)
        return
    native = (project_preview.preview_project_properties(ctx, definition) if isinstance(definition, ProjectUpdate)
              else project_preview.preview_project_definition(ctx, definition))
    selection = native.get("connectionSelection", {})
    if selection and selection.get("status") != "selected":
        _ask(state, "connection", "Выберите подключение приемника.", [
            {"id": "connection:" + str(index), "label": choice["name"], "value": choice["name"]}
            for index, choice in enumerate(selection.get("choices", []))
        ])
        if not state["options"]:
            state.update(stage="blocked", decision=failure_decision("CONNECTION_CONFIGURATION_REQUIRED"))
        return
    if selection.get("connectionName") and "receiverRepair" not in state["payload"]:
        bind_project_connection(state["payload"], selection["connectionName"])
    state["summary"] = preview_response(native)
    if native.get("approved") is not True:
        state.update(stage="blocked", decision=state["summary"].get("decision"))
        return
    state.update(planId=native["planId"], action=native["action"])
    if state["selectFields"]:
        plan = ctx.operations.get_plan(state["planId"])
        fields = preview_fields_page(plan.preview, 0, 200)
        state["choicesComplete"] = fields.get("nextCursor") is None
        state["requiredFields"] = [field["source"] for field in fields["items"]
                                   if isinstance(field.get("source"), str) and any(
                                       field.get(role) is True for role in (
                                           "segmentField", "extendedSegmentField", "initializationSegmentField"))]
        choices = [{"id": "all_fields", "label": "Все штатные поля Помощника"}]
        choices.extend({"id": "field:" + str(index), "label": field["source"],
                        "value": field["source"]} for index, field in enumerate(fields["items"])
                       if isinstance(field.get("source"), str) and field["source"] not in state["requiredFields"])
        _ask(state, "fields", "Выберите поля выгрузки.", choices, multiple=True)
    else:
        _ask(state, "approve", "Применить подготовленный план?", [
            {"id": "approve", "label": "Применить"}, {"id": "cancel", "label": "Отменить"}])


def _yes_no(state: dict, stage: str, question: str) -> None:
    _ask(state, stage, question, [{"id": "yes", "label": "Да"}, {"id": "no", "label": "Нет"}])


def _operation(ctx: AppContext, state: dict, key: str, tool: str, plan_id: str) -> bool:
    operation = state.setdefault(key + "Operation", str(uuid4()))
    try:
        status = operation_status.operation_status(ctx, operation)
    except ApplicationError:
        # Only a missing local operation is a call invitation. A failed DCC
        # reconciliation for a known operation must retain its original ID.
        try:
            ctx.operations.operation_status(operation)
        except OperationStoreError:
            try:
                plan = ctx.operations.get_plan(plan_id)
            except PlanExpired:
                state["expiredAction"] = key
                raise
            if plan.operation_id is not None and plan.operation_id != operation:
                state.update(stage="blocked", decision=failure_decision("PLAN_CONSUMED"))
                state["nextCall"] = {"tool": "get_operation_status",
                                     "arguments": {"operation_id": plan.operation_id}}
                return False
            state["nextCall"] = {"tool": tool, "arguments": {"plan_id": plan_id, "operation_id": operation}}
            return False
        raise
    if status["status"] != "completed":
        state["decision"] = failure_decision(
            status.get("result", {}).get("code") if isinstance(status.get("result"), dict) else None,
            uncertain=status["status"] in {"in_progress", "indeterminate"})
        state["nextCall"] = {"tool": "get_operation_status", "arguments": {"operation_id": operation}}
        if status["status"] == "rejected":
            state["stage"] = "blocked"
        return False
    state.pop("nextCall", None)
    state.pop("decision", None)
    native = status.get("result")
    # The immutable operation journal owns full native results and snapshots.
    # A dialogue needs only the identity, verification and execution outcome.
    state["lastResult"] = ({key: native[key] for key in (
        "projectRef", "verification", "taskId", "launchStatus", "status", "blockers", "code", "message")
                           if key in native} if isinstance(native, dict) else None)
    return True


def _launch(ctx: AppContext, state: dict, action: str) -> bool:
    key = action + "Plan"
    if key not in state:
        preview = execution.preview_execution(ctx, state["projectGuid"], action)
        if preview.get("approved") is not True:
            blockers = preview.get("summary", {}).get("blockers", [])
            if action == "initialization" and blockers and all(code == "NO_ENABLED_QUEUE_ROWS" for code in blockers):
                state["decision"] = failure_decision("NO_ENABLED_QUEUE_ROWS")
                _ask(state, "initialization_not_applicable", "У проекта нет строк очереди. Продолжить без инициализации?",
                     [{"id": "continue", "label": "Продолжить"}])
            else:
                state.update(stage="blocked", decision=failure_decision(
                    blockers[0] if blockers and isinstance(blockers[0], str) else None),
                    nativeBlockers=blockers)
            return False
        state[key] = preview["planId"]
    tool = "initialize_project_queue" if action == "initialization" else "start_project_export"
    if not _operation(ctx, state, action, tool, state[key]):
        return False
    launch = state["lastResult"]
    if not isinstance(launch, dict) or not is_uuid(launch.get("taskId")):
        state.update(stage="blocked", decision=failure_decision(None))
        return False
    state["taskId"] = launch["taskId"]
    if action == "export" and state["wait"] is False:
        state["stage"] = "launched"
        return False
    status = execution.export_status(ctx, state["projectGuid"], state["taskId"], False, wait_seconds=2)
    state["task"] = status
    if status.get("status") == "succeeded":
        state[action + "Done"] = True
        if action == "export":
            state["stage"] = "complete"
        return True
    if status.get("status") in {"failed", "cancelled", "recovery_required", "unknown"}:
        state.update(stage="blocked", decision=failure_decision("TASK_STATUS_UNAVAILABLE"))
    state["nextCall"] = {"tool": "get_project_plan", "arguments": {"plan_id": state["scenarioId"]}}
    return False


def _view(state: dict) -> dict:
    result = {key: state[key] for key in (
        "scenarioId", "revision", "stage", "question", "multiple", "choicesComplete",
        "summary", "decision", "nextCall", "projectGuid", "taskId", "task", "diagnostic",
        "requiredFields", "nativeBlockers", "metadataDecision") if key in state}
    result["choices"] = [{key: option[key] for key in ("id", "label")} for option in state.get("options", [])]
    if state.get("options"):
        result["reply"] = {"tool": "preview_project", "arguments": {"scenario": {
            "scenarioId": state["scenarioId"], "revision": state["revision"], "choice": "<choice-id>"}}}
    return result
