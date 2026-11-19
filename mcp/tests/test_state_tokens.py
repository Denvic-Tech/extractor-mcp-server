from extractor1c.application.responses import operation_response
from extractor1c.interface.mcp.context import CONTEXT
from extractor1c.domain.projects.changes import ProjectUpdate
import json
from uuid import uuid4

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from extractor1c import mcp_server as server
from extractor1c.interface.mcp.context import _state_scope as state_scope
from extractor1c.infrastructure.operation_store import OperationStore
from extractor1c.application.state_tokens import resolve_state, short_token


def saved(comment="before"):
    value = {"projectName": "P", "projectRef": "guid", "comment": comment,
             "source": "Справочник.Валюты", "table": "currencies", "fields": [
                 {"source": "Наименование", "target": "Name", "type": "varchar"}]}
    return {**value, "stateToken": json.dumps(value, ensure_ascii=False)}


def test_compact_default_and_explicit_details(monkeypatch):
    value = saved()
    monkeypatch.setattr(server.runtime, "call_api", lambda *_: value)
    compact = server.verify_project("P")
    details = server.verify_project("P", details=True)
    assert "fields" not in compact and "fields" not in compact["rows"][0]
    assert compact["stateToken"] == details["stateToken"]
    assert len(compact["stateToken"]) < 100
    assert details["fields"] == value["fields"]
    assert server.verify_project_summary("P")["stateToken"] == compact["stateToken"]


@pytest.mark.parametrize("scope,project", [("other-base", "P"), ("base", "other-project")])
def test_token_cannot_cross_base_or_project(scope, project):
    value = saved()
    token = short_token(value["stateToken"], "base", "P")
    with pytest.raises(ValueError, match="Project changed"):
        resolve_state({"projectName": project, "expectedState": token}, lambda *_: value, scope)


def test_project_selector_case_does_not_change_the_token():
    assert short_token("native", "base", "Project") == short_token("native", "base", "PROJECT")


def test_oversized_native_snapshot_is_rejected_before_mutation(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    monkeypatch.setattr(server.runtime, "_has_direct_capability", lambda: True)
    native = "x" * 524289
    token = short_token(native, state_scope(), "P")

    def read_only(route, body):
        assert route == "projects/verify", "Oversized snapshot must not reach a write"
        return {"stateToken": native}

    monkeypatch.setattr(server.runtime, "call_api", read_only)
    operation = str(uuid4())
    with pytest.raises(ToolError, match="expectedState limit"):
        server.update_project(operation_id=operation, change={
            "projectName": "P", "expectedState": token, "comment": "not applied"})
    assert store.operation_status(operation)["status"] == "rejected"


def test_short_update_race_rejection_and_offline_replay(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    monkeypatch.setattr(server.runtime, "_has_direct_capability", lambda: True)
    current = saved()
    writes = []
    race = False

    def backend(route, body):
        nonlocal current
        if route == "projects/verify":
            return current
        assert route == "projects/update"
        if race:
            current = saved("concurrent")
        # Model the existing locked EPA comparison, including the read/write race.
        if body["expectedState"] != current["stateToken"]:
            raise ToolError("Project changed")
        writes.append(body)
        current = saved(body["comment"])
        return current

    monkeypatch.setattr(server.runtime, "call_api", backend)
    token = server.verify_project("P")["stateToken"]
    operation = str(uuid4())
    change = {"projectName": "P", "expectedState": token, "comment": "after"}
    result = server.update_project(operation_id=operation, change=change)
    assert len(writes) == 1 and not writes[0]["expectedState"].startswith("mcp-state-")
    assert "fields" not in result["result"]
    monkeypatch.setattr(server.runtime, "call_api", lambda *_: pytest.fail("Replay must be offline"))
    assert server.update_project(operation_id=operation, change=change)["replayed"]
    monkeypatch.setattr(server.runtime, "call_api", backend)
    with pytest.raises(ToolError, match="Project changed"):
        server.update_project(operation_id=str(uuid4()), change=change)
    assert len(writes) == 1
    fresh = server.verify_project("P")["stateToken"]
    race = True
    with pytest.raises(ToolError, match="Project changed"):
        server.update_project(operation_id=str(uuid4()), change={**change, "expectedState": fresh})
    assert len(writes) == 1


def test_operation_envelopes_do_not_leak_native_snapshots():
    value = saved()
    result = operation_response(CONTEXT, {"status": "completed", "result": value,
                                         "dcc": {"result": value}})
    for projected in [result["result"], result["dcc"]["result"]]:
        assert "fields" not in projected
        assert len(projected["stateToken"]) < 100


def test_read_failure_is_rejected_before_write(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    monkeypatch.setattr(server.runtime, "_has_direct_capability", lambda: True)
    operation = str(uuid4())
    token = short_token(saved()["stateToken"], state_scope(), "P")

    def unavailable(route, body):
        assert route == "projects/verify"
        raise ToolError("read unavailable")

    monkeypatch.setattr(server.runtime, "call_api", unavailable)
    with pytest.raises(ToolError, match="read unavailable"):
        server.update_project(operation_id=operation, change={
            "projectName": "P", "expectedState": token, "comment": "never applied"})
    status = store.operation_status(operation)
    assert status["status"] == "rejected"
    assert status["result"]["writeApplied"] is False


def test_preview_plan_keeps_short_token_and_rechecks_at_commit(monkeypatch, tmp_path):
    store = OperationStore(tmp_path / "ops.sqlite")
    monkeypatch.setattr(server.runtime, "operation_store", lambda: store)
    monkeypatch.setattr(server.runtime, "_has_direct_capability", lambda: True)
    current = saved()

    def backend(route, body):
        assert route == "projects/verify", "Conflicting plan must not write"
        return current

    monkeypatch.setattr(server.runtime, "call_api", backend)
    token = server.verify_project("P")["stateToken"]
    preview = server.preview_project_properties(ProjectUpdate(
        projectName="P", expectedState=token, comment="requested"))
    assert preview["approved"]
    assert store.get_plan(preview["planId"]).payload["expectedState"] == token
    current = saved("another writer")
    with pytest.raises(ToolError, match="Project changed"):
        server.update_project(plan_id=preview["planId"], operation_id=str(uuid4()))
