"""Cancellation targets an immutable task and never implies export completion."""
from unittest.mock import patch
import sqlite3
import uuid

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from extractor1c import mcp_server as server
from extractor1c.operation_store import OperationStore

GUID = "4eac24a8-18ba-4ad0-a595-2dba2017bf14"
TASK = "2e129faa-5715-4be0-94b7-36833249e5fc"
JOB = "0d217bff-9e86-4200-aa7e-7b37de99d3d4"


@pytest.fixture
def store(tmp_path):
    value = OperationStore(tmp_path / "operations.sqlite3")
    with patch.object(server, "operation_store", return_value=value):
        yield value


def preview():
    with patch.object(server, "call_api", return_value={
        "approved": True, "expectedTaskState": "task-state",
    }) as backend:
        result = server.preview_project_export_cancellation(GUID, TASK)
    backend.assert_called_once_with(
        "projects/export/cancel/preview", {"projectGuid": GUID, "taskId": TASK}
    )
    return result["planId"]


def test_stop_acknowledgement_replays_without_second_request(store):
    plan = preview()
    operation = str(uuid.uuid4())
    response = {"cancelStatus": "requested", "projectGuid": GUID,
                "taskId": TASK, "backgroundJobId": JOB}
    with patch.object(server, "call_api", return_value=response) as backend:
        assert server.cancel_project_export(plan, operation)["result"] == response
        assert server.cancel_project_export(plan, operation)["replayed"]
    backend.assert_called_once_with("projects/export/cancel", store.get_plan(plan).payload)
    assert store.operation_status(operation)["status"] == "completed"
    assert "status" not in response  # A completed request is not a stopped export.


@pytest.mark.parametrize("response", [None, {}, {
    "cancelStatus": "cancelled", "projectGuid": GUID, "taskId": TASK,
}, {"cancelStatus": "requested", "projectGuid": GUID,
    "taskId": JOB, "backgroundJobId": JOB}])
def test_uncertain_result_allows_fresh_stop_plan_without_changing_old_operation(store, response):
    plan = preview()
    operation = str(uuid.uuid4())
    with patch.object(server, "call_api", return_value=response):
        with pytest.raises(ToolError, match="indeterminate"):
            server.cancel_project_export(plan, operation)
    previous = store.operation_status(operation)
    assert previous["status"] == "indeterminate"
    assert previous["result"] == response
    other = preview()
    acknowledgement = {"cancelStatus": "requested", "projectGuid": GUID,
                       "taskId": TASK, "backgroundJobId": JOB}
    with patch.object(server, "call_api", return_value=acknowledgement) as backend:
        result = server.cancel_project_export(other, str(uuid.uuid4()))
    backend.assert_called_once_with("projects/export/cancel", store.get_plan(other).payload)
    assert result["result"] == acknowledgement
    assert store.operation_status(operation) == previous


def test_uncertain_operation_id_does_not_repeat_native_request(store):
    plan = preview()
    operation = str(uuid.uuid4())
    with patch.object(server, "call_api", side_effect=ToolError("1C API HTTP 409")):
        with pytest.raises(ToolError, match="409"):
            server.cancel_project_export(plan, operation)
    with patch.object(server, "call_api") as backend:
        with pytest.raises(ToolError, match="indeterminate"):
            server.cancel_project_export(plan, operation)
    backend.assert_not_called()
    assert store.operation_status(operation)["status"] == "indeterminate"


def test_in_progress_stop_still_blocks_another_request(store):
    plan = preview()
    operation = str(uuid.uuid4())
    store.begin(plan, operation, "cancel")
    other = preview()
    with patch.object(server, "call_api") as backend:
        with pytest.raises(ToolError, match="unresolved cancellation"):
            server.cancel_project_export(other, str(uuid.uuid4()))
    backend.assert_not_called()
    assert store.operation_status(operation)["status"] == "in_progress"


@pytest.mark.parametrize("reason", ["CANCELLATION_ALREADY_REQUESTED", "STATE_CHANGED"])
def test_native_recheck_refusal_after_uncertain_stop_is_not_success(store, reason):
    plan = preview()
    operation = str(uuid.uuid4())
    store.begin(plan, operation, "cancel")
    store.mark_indeterminate(operation)
    previous = store.operation_status(operation)
    other = preview()
    new_operation = str(uuid.uuid4())
    # The old request could set the stop flag after the new preview. The native
    # locked recheck must remain authoritative instead of fabricating success.
    with patch.object(server, "call_api", side_effect=ToolError("1C API HTTP 409: " + reason)) as backend:
        with pytest.raises(ToolError, match=reason):
            server.cancel_project_export(other, new_operation)
    backend.assert_called_once_with("projects/export/cancel", store.get_plan(other).payload)
    assert store.operation_status(new_operation)["status"] == "indeterminate"
    assert store.operation_status(operation) == previous


def test_unknown_task_preview_creates_no_plan(store):
    with patch.object(server, "call_api", return_value={
        "approved": False, "blockers": ["BACKGROUND_JOB_NOT_FOUND"],
    }):
        assert server.preview_project_export_cancellation(GUID, TASK)["planId"] is None


def test_reopen_preserves_completed_operation(store):
    plan = preview()
    operation = str(uuid.uuid4())
    store.begin(plan, operation, "cancel")
    store.complete(operation, {"cancelStatus": "requested"})
    reopened = OperationStore(store.path)
    assert reopened.operation_status(operation)["result"] == {"cancelStatus": "requested"}


def test_upgrade_preserves_previous_launch_history(tmp_path):
    source = OperationStore(tmp_path / "source.sqlite3")
    plan = source.create_plan("export", {"projectGuid": GUID}, {}, 60)
    operation = str(uuid.uuid4())
    source.begin(plan.plan_id, operation, "export")
    source.complete(operation, {"launchStatus": "started", "taskId": TASK})
    with sqlite3.connect(source.path) as connection:
        old_schema_dump = "\n".join(connection.iterdump()).replace(", 'cancel'", "")
    target = tmp_path / "previous.sqlite3"
    with sqlite3.connect(target) as connection:
        connection.executescript(old_schema_dump)
    upgraded = OperationStore(target)
    assert upgraded.operation_status(operation)["result"]["taskId"] == TASK
    assert upgraded.get_plan(plan.plan_id).operation_id == operation
    cancel = upgraded.create_plan("cancel", {"projectGuid": GUID, "taskId": TASK}, {}, 60)
    upgraded.begin(cancel.plan_id, str(uuid.uuid4()), "cancel")
