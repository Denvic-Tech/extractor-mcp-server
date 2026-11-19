"""Scenario persistence, compatible choices and recovery without duplicate writes."""
from copy import deepcopy
from uuid import uuid4

import pytest

from extractor1c.application.errors import ApplicationError, GatewayError
from extractor1c.application.project_scenarios import read_scenario, scenario
from extractor1c.domain.operations.errors import OperationStoreError
from extractor1c.domain.projects.definition import ProjectDefinition
from extractor1c.domain.projects.changes import ProjectUpdate
from extractor1c.domain.projects.scenario import ScenarioInput
from extractor1c.infrastructure.operation_store import OperationStore


class Context:
    transport = "direct_http"
    plan_ttl_seconds = 1800
    state_scope = "test"

    def __init__(self, path):
        self.operations = OperationStore(path)
        self.calls = []
        self.block_initialization = False
        self.project = str(uuid4())

    def call(self, route, body, **_):
        self.calls.append((route, deepcopy(body)))
        if route == "connections/list":
            return [{"name": "pg", "type": "postgresql"}]
        if route == "metadata/describe":
            return [{"name": "Code", "type": "String", "metadataTargets": []}]
        if route == "projects/preview":
            return {"projectName": "P", "projectExists": False, "tableExists": False,
                    "fields": [{"source": "Code", "target": "Code", "type": "native-type"}]}
        if route == "projects/initialization/preview" and self.block_initialization:
            return {"approved": False, "blockers": ["NO_ENABLED_QUEUE_ROWS"]}
        if route in {"projects/initialization/preview", "projects/export/preview"}:
            return {"approved": True, "expectedProjectState": "native-state"}
        if route == "projects/export/status":
            return {"status": "succeeded", "taskId": body["taskId"]}
        pytest.fail("Scenario must not perform business writes: " + route)


def choose(ctx, response, choice):
    return scenario(ctx, ScenarioInput(scenarioId=response["scenarioId"],
                                     revision=response["revision"], choice=choice))


def start(ctx, **decisions):
    return scenario(ctx, ScenarioInput(**decisions),
                    ProjectDefinition(projectName="P", object="Справочник.Валюты", tableName="currencies"))


def record_result(ctx, invitation, result):
    args = invitation["nextCall"]["arguments"]
    plan, replay = ctx.operations.begin(args["plan_id"], args["operation_id"],
                                        ctx.operations.get_plan(args["plan_id"]).action)
    assert replay is None
    ctx.operations.complete(args["operation_id"], result)
    return plan


def to_write(ctx, **decisions):
    current = start(ctx, **decisions)
    assert current["stage"] == "segments"
    assert all("type" not in choice for choice in current["choices"])
    current = choose(ctx, current, "no_segments")
    assert current["stage"] == "fields"
    current = choose(ctx, current, "all_fields")
    assert current["stage"] == "approve"
    return choose(ctx, current, "approve")


def test_saved_scenario_restores_the_same_write_after_restart(tmp_path):
    path = tmp_path / "ops.sqlite"
    ctx = Context(path)
    response = to_write(ctx, initialize=False, export=False)
    assert response["nextCall"]["tool"] == "create_project"
    restored = Context(path)
    same = read_scenario(restored, response["scenarioId"])
    assert same["nextCall"] == response["nextCall"]
    record_result(restored, same, {"projectRef": ctx.project, "verification": {"status": "partial"}})
    final = read_scenario(restored, response["scenarioId"])
    assert final["stage"] == "complete"
    assert final["projectGuid"] == ctx.project
    assert not any(route.endswith("/create") for route, _ in ctx.calls + restored.calls)


def test_stale_choice_does_not_change_scenario_or_create_native_plan(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    current = start(ctx)
    choose(ctx, current, "no_segments")
    calls = len(ctx.calls)
    conflict = choose(ctx, current, "native_defaults")
    assert conflict["decision"]["code"] == "SCENARIO_REVISION_CONFLICT"
    assert conflict["nextCall"]["arguments"] == {"plan_id": current["scenarioId"]}
    assert len(ctx.calls) == calls
    with pytest.raises(OperationStoreError, match="changed"):
        ctx.operations.save_scenario({"scenarioId": current["scenarioId"]}, current["revision"])


def test_incompatible_field_choice_is_rejected_before_native_call(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    current = choose(ctx, start(ctx), "no_segments")
    before = len(ctx.calls)
    with pytest.raises(ApplicationError, match="compatible choices"):
        choose(ctx, current, "invented SQL column type")
    assert len(ctx.calls) == before
    cancelled = choose(ctx, choose(ctx, current, "all_fields"), "cancel")
    assert read_scenario(Context(tmp_path / "ops.sqlite"), cancelled["scenarioId"])["stage"] == "cancelled"


def test_enum_without_queue_has_explicit_recovery_and_same_export_operation(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    ctx.block_initialization = True
    write = to_write(ctx, initialize=True, export=True, wait=True)
    record_result(ctx, write, {"projectRef": ctx.project, "verification": {"status": "matched"}})
    stopped = read_scenario(ctx, write["scenarioId"])
    assert stopped["stage"] == "initialization_not_applicable"
    assert stopped["decision"]["code"] == "NO_ENABLED_QUEUE_ROWS"
    export = choose(ctx, stopped, "continue")
    assert export["nextCall"]["tool"] == "start_project_export"
    assert read_scenario(ctx, export["scenarioId"])["nextCall"] == export["nextCall"]
    task = str(uuid4())
    record_result(ctx, export, {"taskId": task, "launchStatus": "started"})
    final = read_scenario(ctx, export["scenarioId"])
    assert final["stage"] == "complete" and final["taskId"] == task


def test_uncertain_write_invites_reconciliation_without_another_write_id(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    write = to_write(ctx)
    args = write["nextCall"]["arguments"]
    ctx.operations.begin(args["plan_id"], args["operation_id"], "create")
    ctx.operations.mark_indeterminate(args["operation_id"])
    state = read_scenario(ctx, write["scenarioId"])
    assert state["nextCall"] == {
        "tool": "get_operation_status", "arguments": {"operation_id": args["operation_id"]}}
    assert state["decision"]["retryAllowed"] is False
    assert read_scenario(ctx, write["scenarioId"])["nextCall"] == state["nextCall"]


def test_scenarios_are_isolated_in_the_existing_scoped_journal(tmp_path):
    first = Context(tmp_path / "first.sqlite")
    value = start(first)
    with pytest.raises(ApplicationError, match="not found"):
        read_scenario(Context(tmp_path / "other.sqlite"), value["scenarioId"])


def test_native_segment_role_fields_are_preserved_in_partial_selection(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    base_call = ctx.call
    def native(route, body, **kwargs):
        result = base_call(route, body, **kwargs)
        if route == "projects/preview":
            result["fields"].append({"source": "QueueKey", "target": "QueueKey",
                                     "type": "native-type", "segmentField": True})
        return result
    ctx.call = native
    state = choose(ctx, start(ctx), "no_segments")
    assert state["requiredFields"] == ["QueueKey"]
    optional = next(choice["id"] for choice in state["choices"] if choice["label"] == "Code")
    approved = choose(ctx, choose(ctx, state, [optional]), "approve")
    plan = ctx.operations.get_plan(approved["nextCall"]["arguments"]["plan_id"])
    assert plan.payload["selectedFields"] == ["QueueKey", "Code"]


def test_rejected_operation_has_a_blocking_recovery_instead_of_a_new_write(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    write = to_write(ctx)
    args = write["nextCall"]["arguments"]
    ctx.operations.begin(args["plan_id"], args["operation_id"], "create")
    ctx.operations.reject(args["operation_id"], {"code": "PROJECT_STATE_CONFLICT", "writeApplied": False})
    state = read_scenario(ctx, write["scenarioId"])
    assert state["stage"] == "blocked"
    assert state["decision"]["nextAction"] == "refresh_project_and_review_changes"
    assert state["nextCall"]["arguments"]["operation_id"] == args["operation_id"]


def test_native_refusal_without_schema_conflict_cannot_produce_a_write_plan(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    base_call = ctx.call
    def refused(route, body, **kwargs):
        result = base_call(route, body, **kwargs)
        if route == "projects/preview":
            result.update(approved=False, blockers=["NATIVE_REFUSAL"])
        return result
    ctx.call = refused
    state = choose(ctx, start(ctx), "no_segments")
    assert state["stage"] == "blocked"
    assert "NATIVE_REFUSAL" in state["decision"]["blockers"]
    assert "nextCall" not in state


def test_expired_unexecuted_plan_is_reprepared_by_choice_without_changing_write_id(tmp_path, monkeypatch):
    from extractor1c.infrastructure import operation_store
    ctx = Context(tmp_path / "ops.sqlite")
    write = to_write(ctx)
    old = write["nextCall"]["arguments"]
    later = operation_store.time.time() + 3600
    monkeypatch.setattr(operation_store.time, "time", lambda: later)
    expired = read_scenario(ctx, write["scenarioId"])
    assert expired["stage"] == "expired_plan"
    assert expired["decision"]["code"] == "PLAN_EXPIRED"
    refreshed = choose(ctx, expired, "refresh_plan")
    assert refreshed["stage"] == "approve"
    ready = choose(ctx, refreshed, "approve")
    assert ready["nextCall"]["arguments"]["operation_id"] == old["operation_id"]
    assert ready["nextCall"]["arguments"]["plan_id"] != old["plan_id"]


def test_property_update_scenario_finishes_without_unrequested_initialization_or_export(tmp_path):
    ctx = Context(tmp_path / "ops.sqlite")
    base_call = ctx.call
    ctx.call = lambda route, body, **kwargs: (
        {"stateToken": "native"} if route == "projects/verify" else base_call(route, body, **kwargs))
    state = scenario(ctx, ScenarioInput(), ProjectUpdate(
        projectName="P", expectedState="native", comment="updated"))
    write = choose(ctx, state, "approve")
    assert write["nextCall"]["tool"] == "update_project"
    record_result(ctx, write, {"projectRef": ctx.project, "verification": {"status": "matched"}})
    final = read_scenario(ctx, state["scenarioId"])
    assert final["stage"] == "complete"
    assert not any("initialization" in route or "export" in route for route, _ in ctx.calls)


@pytest.mark.parametrize("status", [404, 403])
def test_absent_optional_snapshot_offers_native_helper_but_access_denial_stays_blocked(tmp_path, status):
    ctx = Context(tmp_path / "ops.sqlite")
    ctx.transport = "dcc"
    def snapshot(*args, **kwargs):
        raise GatewayError("snapshot unavailable", status=status, code="DCC_METADATA_UNAVAILABLE")
    ctx.read_snapshot = snapshot
    state = start(ctx)
    if status == 403:
        assert state["stage"] == "blocked" and state["choices"] == []
        assert not any(route == "projects/preview" for route, _ in ctx.calls)
    else:
        assert state["stage"] == "segments"
        assert state["choicesComplete"] is False
        assert [choice["id"] for choice in state["choices"]] == ["native_defaults", "no_segments"]
        fields = choose(ctx, state, "native_defaults")
        assert fields["stage"] == "fields"
        assert ctx.transport == "dcc"
