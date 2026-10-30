"""Export request contracts and no-duplicate-launch guarantees without a live export."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import sqlite3
import uuid

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from extractor1c import mcp_server as server
from extractor1c.operation_store import OperationStore, OperationIndeterminate
from extractor1c.projects_api import (
    ApiSettings, ProjectsApiClient, ProjectExportRef, ProjectExportStart, ProjectExportStatus,
)

GUID = "4eac24a8-18ba-4ad0-a595-2dba2017bf14"
TASK = "2e129faa-5715-4be0-94b7-36833249e5fc"
JOB = "0d217bff-9e86-4200-aa7e-7b37de99d3d4"


@pytest.fixture
def store(tmp_path):
    value = OperationStore(tmp_path / "operations.sqlite3")
    with patch.object(server, "operation_store", return_value=value):
        yield value


def preview(project=GUID):
    response = {"approved": True, "expectedProjectState": "project-version", "executionUser": "http-user"}
    with patch.object(server, "call_api", return_value=response) as backend:
        result = server.preview_project_export(project)
    backend.assert_called_once_with("projects/export/preview", {"projectGuid": project})
    return result["planId"]


@pytest.mark.parametrize("model,payload", [
    (ProjectExportRef, {"projectGuid": "project-name"}),
    (ProjectExportRef, {"projectGuid": GUID, "code": "arbitrary"}),
    (ProjectExportRef, {"projectGuid": GUID, "url": "https://other.test"}),
    (ProjectExportStart, {"projectGuid": GUID}),
    (ProjectExportStart, {"projectGuid": GUID, "expectedProjectState": ""}),
    (ProjectExportStatus, {"projectGuid": GUID, "taskId": "wrong"}),
])
def test_closed_contract(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize("response", [None, [], {"approved": True}, {"approved": True, "expectedProjectState": ""}])
def test_malformed_preview_does_not_create_plan(store, response):
    with patch.object(server, "call_api", return_value=response), pytest.raises(ToolError):
        server.preview_project_export(GUID)


def test_blocked_preview_does_not_create_plan(store):
    with patch.object(server, "call_api", return_value={"approved": False, "blockers": ["EXPORT_FORBIDDEN"]}):
        assert server.preview_project_export(GUID)["planId"] is None


def test_preview_binds_original_project_and_completed_launch_replays(store):
    plan_id = preview()
    plan = store.get_plan(plan_id)
    assert plan.payload == {"projectGuid": GUID, "expectedProjectState": "project-version"}
    assert server.get_project_plan(plan_id)["action"] == "export"
    operation = str(uuid.uuid4())
    response = {"launchStatus": "started", "taskId": TASK, "backgroundJobId": JOB}
    with patch.object(server, "call_api", return_value=response) as backend:
        assert server.start_project_export(plan_id, operation)["result"] == response
        assert server.start_project_export(plan_id, operation)["replayed"]
    backend.assert_called_once_with("projects/export/start", plan.payload)
    assert server.get_operation_status(operation)["status"] == "completed"
    with patch.object(server, "call_api") as backend, pytest.raises(ToolError):
        server.update_project(plan_id, str(uuid.uuid4()))
    backend.assert_not_called()


@pytest.mark.parametrize("response", [
    None, {}, {"launchStatus": "unknown", "taskId": TASK},
    {"launchStatus": "started"}, {"launchStatus": "started", "taskId": TASK},
    {"launchStatus": "started", "taskId": TASK, "backgroundJobId": "invalid"},
    {"launchStatus": "already_running", "taskId": "invalid"},
])
def test_unconfirmed_launch_retains_evidence_and_blocks_fresh_plan(store, response):
    plan_id = preview()
    operation = str(uuid.uuid4())
    with patch.object(server, "call_api", return_value=response) as backend:
        with pytest.raises(ToolError, match="indeterminate"):
            server.start_project_export(plan_id, operation)
        with pytest.raises(ToolError, match="indeterminate"):
            server.start_project_export(plan_id, operation)
        assert backend.call_count == 1
    state = server.get_operation_status(operation)
    assert state["status"] == "indeterminate"
    assert state["result"] == response
    other_plan = preview(GUID.upper())
    with patch.object(server, "call_api") as backend, pytest.raises(ToolError, match="unresolved"):
        server.start_project_export(other_plan, str(uuid.uuid4()))
    backend.assert_not_called()


def test_network_loss_blocks_only_same_project_and_status_remains_available(store):
    plan_id = preview()
    with patch.object(server, "call_api", side_effect=ToolError("connection lost")):
        with pytest.raises(ToolError):
            server.start_project_export(plan_id, str(uuid.uuid4()))
    other_plan = preview(str(uuid.uuid4()))
    with patch.object(server, "call_api", return_value={"launchStatus": "rejected"}):
        assert server.start_project_export(other_plan, str(uuid.uuid4()))["result"]["launchStatus"] == "rejected"
    with patch.object(server, "call_api", return_value={"status": "failed", "taskId": TASK}) as backend:
        assert server.get_project_export_status(GUID, TASK)["status"] == "failed"
        backend.assert_called_once_with("projects/export/status", {"projectGuid": GUID, "taskId": TASK})


@pytest.mark.parametrize("status", ["already_running", "initializing", "rejected"])
def test_explicit_no_new_launch_outcomes_replay(store, status):
    plan_id = preview()
    operation = str(uuid.uuid4())
    with patch.object(server, "call_api", return_value={"launchStatus": status, "taskId": TASK}) as backend:
        server.start_project_export(plan_id, operation)
        assert server.start_project_export(plan_id, operation)["replayed"]
        assert backend.call_count == 1


def test_concurrent_distinct_plans_cannot_launch_same_project_twice(store):
    plans = [preview(), preview()]

    def begin(plan_id):
        try:
            store.begin(plan_id, str(uuid.uuid4()), "export")
            return "started"
        except OperationIndeterminate:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(begin, plans)) == ["blocked", "started"]


@pytest.mark.parametrize("old_actions", ["'create', 'update'", "'create', 'update', 'schedule'"])
def test_upgrade_pre_export_store_preserves_replay_and_constraints(tmp_path, old_actions):
    path = tmp_path / "legacy.sqlite3"
    old = OperationStore(path)
    plan = old.create_plan("update", {}, {}, 300)
    operation = str(uuid.uuid4())
    old.begin(plan.plan_id, operation, "update")
    old.complete(operation, {"ok": True})
    with sqlite3.connect(path) as connection:
        for table in ("plans", "operations"):
            schema = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            schema = schema.replace("'create', 'update', 'schedule', 'export', 'initialization'", old_actions)
            connection.execute(f"CREATE TEMP TABLE {table}_backup AS SELECT * FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(schema)
            connection.execute(f"INSERT INTO {table} SELECT * FROM {table}_backup")
    upgraded = OperationStore(path)
    assert upgraded.begin(plan.plan_id, operation, "update")[1] == {"ok": True}
    for action in ("schedule", "export"):
        added = upgraded.create_plan(action, {"projectGuid": GUID}, {}, 300)
        upgraded.begin(added.plan_id, str(uuid.uuid4()), action)
    with sqlite3.connect(path) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_client_uses_fixed_post_and_does_not_retry_uncertain_start():
    session = Mock()
    session.post.side_effect = TimeoutError("response lost")
    client = ProjectsApiClient(ApiSettings(_env_file=None, url="https://example.test/base/hs/extractor-projects/v1",
                                          user="test", password="test"), session)
    with pytest.raises(TimeoutError):
        client.call("projects/export/start", {"projectGuid": GUID, "expectedProjectState": "version"})
    assert session.post.call_count == 1
    assert session.post.call_args.args[0].endswith("/v1/projects/export/start")
    assert session.post.call_args.kwargs["allow_redirects"] is False
