from copy import deepcopy
import json
from uuid import uuid4

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from extractor1c import mcp_server as server
from extractor1c.domain.decisions import (
    field_choices, receiver_decision, plan_decision, task_decision, graph_choices, confirm_saved,
)
from extractor1c.infrastructure.operation_store import OperationStore


def test_segment_choices_reject_compounds_and_unknown_types():
    page = {"nextCursor": 10, "items": [
        {"name": "Период", "type": "Дата"},
        {"name": "Ссылка", "type": "СправочникСсылка.Валюты", "metadataTargets": ["Справочник.Валюты"]},
        {"name": "Mixed", "types": [{"name": "String"}, {"name": "Number"}]},
        {"name": "Unknown", "type": "Unknown", "metadataTargets": ["Справочник.Валюты"]},
    ]}
    result = field_choices(page, "РегистрСведений.КурсыВалют.СрезПоследних")
    assert result["segmentChoices"][0]["recommended"] == "Месяц"
    assert result["segmentChoices"][1]["eligible"] is True
    assert result["segmentChoices"][2]["reason"] == "COMPOUND_SEGMENT_FORBIDDEN"
    assert result["segmentChoices"][3]["eligible"] is False
    assert result["segmentChoices"][3]["exportDecision"] == "native_validation_required"
    assert result["choicesComplete"] is False
    assert "segmentChoices" not in page


def test_receiver_reports_unknown_types_without_claiming_equality():
    native = {"sourceEqualityVerified": False, "checks": {"schema": {"status": "ok", "result": {
        "exists": True, "columns": [{"name": "a"}],
        "mappingDifferences": {"missing": [], "extra": [], "typeComparison": "unknown"}}}}}
    original = deepcopy(native)
    compact = receiver_decision(native)
    assert compact["verification"]["status"] == "observed"
    assert compact["verification"]["typesVerified"] is False
    assert "columns" not in compact["checks"]["schema"]["result"]
    assert receiver_decision(native, True)["checks"] == native["checks"]
    assert original == native
    native["checks"]["schema"]["result"]["mappingDifferences"]["missing"] = ["a"]
    assert receiver_decision(native)["verification"]["status"] == "differences"


def test_receiver_uses_native_comparison_without_interpreting_sql_types():
    native = {"receiver": {"type": "postgresql"}, "checks": {"schema": {"status": "ok", "result": {
        "exists": True, "mappingDifferences": {"missing": [], "extra": [], "typeComparison": "unknown"},
        "columns": [{"name": "amount", "declaredSqlType": "numeric(10,2)", "expectedProjectType": "numeric",
                     "expectedPrecision": 10, "expectedScale": 2, "nullable": False, "expectedNullable": False}]}}}}
    # Even an apparently obvious SQL declaration cannot override native evidence.
    assert receiver_decision(native)["verification"]["typesVerified"] is False
    column = native["checks"]["schema"]["result"]["columns"][0]
    column["typeComparison"] = "match"
    column["declaredSqlType"] = "opaque receiver-specific declaration"
    assert receiver_decision(native)["verification"]["typesVerified"] is True
    column["typeComparison"] = "mismatch"
    result = receiver_decision(native)["verification"]
    assert result["status"] == "differences" and result["typeDifferences"][0]["field"] == "amount"
    native["receiver"]["type"] = "unknown"
    column["typeComparison"] = "match"
    assert receiver_decision(native)["verification"]["typesVerified"] is True


def test_plan_does_not_invent_approval_or_lose_native_blockers():
    assert plan_decision({})["status"] == "blocked"
    assert plan_decision({"approved": True, "stateMatches": False})["status"] == "blocked"
    assert "NATIVE_BLOCKER" in plan_decision({"approved": False, "blockers": ["NATIVE_BLOCKER"]})["blockers"]
    assert plan_decision({"approved": True})["status"] == "ready"


def test_error_deduplication_preserves_explicit_details():
    value = {"status": "failed", "message": "Failure\nFailure\nFailure"}
    assert task_decision(value)["message"] == "Failure"
    assert task_decision(value)["messageTruncated"] is True
    assert task_decision(value, True)["message"] == value["message"]
    assert task_decision(value)["decision"]["restartAllowed"] is False


def test_links_require_keys_and_polymorphic_discriminators():
    result = graph_choices({"complete": True, "truncated": False, "edges": [
        {"path": "A", "target": "Catalog.A", "join": {"sourceField": "A", "targetField": "Ссылка"}},
        {"path": "B", "join": {"requiresValidation": True}},
        {"path": "C", "join": {"sourceField": "C", "targetField": "Ссылка", "requiresTypeDiscriminator": True}},
        {"path": "D", "status": "skipped", "reason": "periodic_register"},
    ]})
    assert [c["decision"] for c in result["choices"]] == [
        "selectable", "needs_key_validation", "choose_type_branch", "excluded"]
    assert "edges" not in result


@pytest.mark.parametrize("action", ["create", "export"])
def test_consumed_plan_cannot_offer_another_commit(monkeypatch, tmp_path, action):
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    payload = {"projectName": "P"} if action == "create" else {"projectGuid": str(uuid4())}
    plan = store.create_plan(action, payload, {"approved": True, "fields": []}, 60)
    operation = str(uuid4())
    store.begin(plan.plan_id, operation, action)
    store.complete(operation, {"ok": True})
    decision = server.get_project_plan(plan.plan_id)["decision"]
    assert decision["status"] == "consumed"
    assert decision["nextAction"] == "get_operation_status"


def test_readback_differences_are_not_requested_echoes(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    monkeypatch.setattr(server.runtime, "_has_direct_capability", lambda: True)
    guid = str(uuid4())
    actual = {"projectName": "P", "projectRef": guid, "comment": "actually saved", "fields": []}
    actual["stateToken"] = json.dumps(actual)
    calls = []

    def native(route, body):
        calls.append(route)
        if route == "projects/update":
            return {"projectRef": guid, "ok": True}
        assert route == "projects/verify"
        return actual

    monkeypatch.setattr(server.runtime, "call_api", native)
    operation = str(uuid4())
    change = {"projectName": "P", "comment": "requested", "expectedState": "legacy snapshot"}
    response = server.update_project(operation_id=operation, change=change)
    verification = response["result"]["verification"]
    assert verification["status"] == "mismatch"
    assert verification["differences"][0]["actual"] == "actually saved"
    assert calls == ["projects/update", "projects/verify"]
    monkeypatch.setattr(server.runtime, "call_api", lambda *_: pytest.fail("replay must not read or write"))
    replay = server.update_project(operation_id=operation, change=change)
    assert replay["result"]["verification"] == verification


def test_saved_roles_compare_bool_and_number_strictly():
    request = {"segmentation": [{"field": "Ref", "segment": "По значению", "regular": True,
                                  "extended": False, "initialization": True}]}
    saved = {"sourceSettings": {"grouping": [{"enabled": True, "path": "Ref", "function": "",
             "regular": 1, "extended": False, "initialization": True}]}}
    assert confirm_saved(request, saved)["status"] == "mismatch"


@pytest.mark.parametrize("wrong_task", [False, True])
def test_server_wait_pins_task_without_restarting(monkeypatch, wrong_task):
    guid, task = str(uuid4()), str(uuid4())
    calls = []

    def status(route, body):
        assert route == "projects/export/status"
        calls.append(body)
        return {"status": "running" if len(calls) == 1 else "succeeded",
                "taskId": str(uuid4()) if wrong_task and len(calls) > 1 else task}

    monkeypatch.setattr(server.runtime, "call_api", status)
    monkeypatch.setattr(server.time, "sleep", lambda _: None)
    if wrong_task:
        with pytest.raises(ToolError, match="Task identity changed"):
            server.get_project_export_status(guid, wait_seconds=1)
    else:
        assert server.get_project_export_status(guid, wait_seconds=1)["status"] == "succeeded"
    assert calls[1]["taskId"] == task


@pytest.mark.parametrize("wait", [-1, 31, True, 1.5, "1"])
def test_invalid_wait_fails_before_network(monkeypatch, wait):
    monkeypatch.setattr(server.runtime, "call_api", lambda *_: pytest.fail("Invalid wait reached network"))
    with pytest.raises(ToolError, match="wait_seconds"):
        server.get_project_export_status(str(uuid4()), wait_seconds=wait)


def test_project_property_readback_does_not_require_selecting_a_group_row():
    result = confirm_saved({"projectName": "P", "comment": "saved"}, {
        "projectName": "P", "comment": "saved", "rows": [{"table": "a"}, {"table": "b"}]})
    assert result["status"] == "matched" and result["differences"] == []


def test_field_readback_selects_the_requested_saved_row_identifier():
    request = {"rowIdentifier": "b", "fieldChanges": [{"source": "Name", "target": "Label"}]}
    saved = {"rows": [{"rowIdentifier": "a", "fields": []}, {
        "rowIdentifier": "b", "fields": [{"source": "Name", "target": "Label"}]}]}
    assert confirm_saved(request, saved)["status"] == "matched"
    request["rowIdentifier"] = "absent"
    assert confirm_saved(request, saved)["status"] == "mismatch"


def test_initializing_task_requests_waiting_on_the_same_task():
    assert task_decision({"status": "initializing"})["decision"]["nextAction"] == "wait_same_task"


@pytest.mark.parametrize("malformed", [False, True])
def test_readback_failure_does_not_make_a_known_write_indeterminate(monkeypatch, tmp_path, malformed):
    from extractor1c.application.errors import ApplicationError
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    monkeypatch.setattr(server.runtime, "_has_direct_capability", lambda: True)
    guid = str(uuid4())
    def native(route, body):
        if route == "projects/update":
            return {"projectRef": guid, "ok": True}
        if malformed:
            return {"projectRef": guid, "rows": [None]}
        raise ApplicationError("readback offline")
    monkeypatch.setattr(server.runtime, "call_api", native)
    operation = str(uuid4())
    response = server.update_project(operation_id=operation, change={
        "projectName": "P", "expectedState": "legacy", "comment": "saved"})
    assert response["result"]["verification"]["status"] == "unavailable"
    assert store.operation_status(operation)["status"] == "completed"


def test_group_property_update_checks_disabled_on_every_saved_row():
    request = {"projectName": "P", "disabled": True}
    saved = {"projectName": "P", "rows": [{"disabled": True}, {"disabled": True}]}
    assert confirm_saved(request, saved)["status"] == "matched"
    saved["rows"][1]["disabled"] = False
    assert confirm_saved(request, saved)["status"] == "mismatch"
