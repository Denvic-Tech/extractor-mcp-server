"""Scheduling contract, immutable plans, migration and uncertain-write behavior."""
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from extractor1c import mcp_server as server
from extractor1c.operation_store import OperationStore, OperationIndeterminate
from extractor1c.projects_api import (
    ApiSettings, ProjectsApiClient, ProjectsApiError, ProjectSchedulePreview,
    ProjectScheduleSet, ScheduleDefinition,
)

GUID = "4eac24a8-18ba-4ad0-a595-2dba2017bf14"


@pytest.mark.parametrize("schedule", [
    {"kind": "daily", "time": "08:30:00"},
    {"kind": "weekly", "time": "23:59:59", "weekdays": [1, 3, 7]},
    {"kind": "interval", "intervalMinutes": 15, "startTime": "09:00:00", "endTime": "18:00:00"},
])
def test_valid_schedule(schedule):
    value = ProjectSchedulePreview(projectGuid=GUID, enabled=True, schedule=schedule)
    assert value.schedule.kind == schedule["kind"]


@pytest.mark.parametrize("schedule", [
    {"kind": "daily"}, {"kind": "daily", "time": "24:00:00"},
    {"kind": "daily", "time": "8:30"},
    {"kind": "daily", "time": "08:30:00", "weekdays": [1]},
    {"kind": "daily", "time": "08:30:00", "intervalMinutes": 10},
    {"kind": "weekly", "time": "08:30:00", "weekdays": []},
    {"kind": "weekly", "time": "08:30:00", "weekdays": [1, 1]},
    {"kind": "weekly", "time": "08:30:00", "weekdays": [0, 8]},
    {"kind": "weekly", "time": "08:30:00", "weekdays": [True]},
    {"kind": "weekly", "time": "08:30:00", "weekdays": ["1"]},
    {"kind": "interval", "intervalMinutes": 0, "startTime": "09:00:00", "endTime": "18:00:00"},
    {"kind": "interval", "intervalMinutes": True, "startTime": "09:00:00", "endTime": "18:00:00"},
    {"kind": "interval", "intervalMinutes": 1.5, "startTime": "09:00:00", "endTime": "18:00:00"},
    {"kind": "interval", "intervalMinutes": 15, "startTime": "18:00:00", "endTime": "09:00:00"},
    {"kind": "interval", "intervalMinutes": 15, "startTime": "09:00:00", "endTime": "09:00:00"},
    {"kind": "interval", "intervalMinutes": 15},
    {"kind": "cron", "expression": "* * * * *"},
    {"kind": "daily", "time": "08:30:00", "code": "arbitrary code"},
])
def test_invalid_schedule(schedule):
    with pytest.raises(ValidationError):
        ScheduleDefinition.model_validate(schedule)


def test_schedule_request_requires_project_state_and_real_boolean():
    with pytest.raises(ValidationError):
        ProjectSchedulePreview(projectGuid="name", enabled=True)
    with pytest.raises(ValidationError):
        ProjectSchedulePreview(projectGuid=GUID, enabled="false")
    with pytest.raises(ValidationError):
        ProjectScheduleSet(projectGuid=GUID, enabled=False)
    assert ProjectSchedulePreview(projectGuid=GUID, enabled=False).schedule is None


@pytest.fixture
def store(tmp_path):
    value = OperationStore(tmp_path / "operations.sqlite3")
    with patch.object(server, "operation_store", return_value=value):
        yield value


def create_plan(enabled=True, schedule=None):
    change = ProjectSchedulePreview(projectGuid=GUID, enabled=enabled, schedule=schedule)
    preview = {"approved": True, "expectedScheduleState": "old-schedule",
               "current": {"enabled": False}, "desired": {"enabled": enabled}}
    with patch.object(server, "call_api", return_value=preview) as backend:
        result = server.preview_project_schedule(change)
        backend.assert_called_once_with("projects/schedule/preview", change.model_dump())
    return result


def test_preview_binds_schedule_and_replays_completed_result(store):
    planned = create_plan(schedule={"kind": "daily", "time": "08:30:00"})
    plan = store.get_plan(planned["planId"])
    assert plan.action == "schedule"
    assert plan.payload["expectedScheduleState"] == "old-schedule"
    assert server.get_project_plan(plan.plan_id)["summary"] == plan.preview
    operation = str(uuid.uuid4())
    actual = {"enabled": True, "jobId": "job", "schedule": {"kind": "daily", "time": "08:30:00"}}
    with patch.object(server, "call_api", return_value=actual) as backend:
        first = server.set_project_schedule(plan.plan_id, operation)
        repeated = server.set_project_schedule(plan.plan_id, operation)
        backend.assert_called_once_with("projects/schedule/set", plan.payload)
    assert first["result"] == actual
    assert repeated["replayed"] is True
    with pytest.raises(ToolError):
        server.set_project_schedule(plan.plan_id, str(uuid.uuid4()))


def test_pause_resume_preserves_settings_and_rejects_wrong_action(store):
    planned = create_plan(enabled=False)
    assert store.get_plan(planned["planId"]).payload["schedule"] is None
    with patch.object(server, "call_api") as backend, pytest.raises(ToolError):
        server.update_project(planned["planId"], str(uuid.uuid4()))
    backend.assert_not_called()


@pytest.mark.parametrize("preview", [
    {"approved": False, "blockers": ["JOB_MISSING"]},
    {"approved": "true", "expectedScheduleState": "state"},
    {"expectedScheduleState": "state"},
])
def test_unapproved_preview_never_issues_plan(store, preview):
    with patch.object(server, "call_api", return_value=preview):
        result = server.preview_project_schedule(ProjectSchedulePreview(projectGuid=GUID, enabled=True))
    assert result["planId"] is None
    assert not result["approved"]


@pytest.mark.parametrize("preview", [None, [], {"approved": True}, {"approved": True, "expectedScheduleState": ""}])
def test_malformed_preview_never_issues_plan(store, preview):
    with patch.object(server, "call_api", return_value=preview), pytest.raises(ToolError):
        server.preview_project_schedule(ProjectSchedulePreview(projectGuid=GUID, enabled=True))


def test_uncertain_schedule_write_is_not_repeated_and_can_be_read_back(store):
    planned = create_plan()
    operation = str(uuid.uuid4())
    with patch.object(server, "call_api", side_effect=ToolError("connection lost")) as backend:
        with pytest.raises(ToolError):
            server.set_project_schedule(planned["planId"], operation)
        with pytest.raises(ToolError, match="indeterminate"):
            server.set_project_schedule(planned["planId"], operation)
        assert backend.call_count == 1
    assert server.get_operation_status(operation)["status"] == "indeterminate"
    with patch.object(server, "call_api", return_value={"enabled": True}) as backend:
        assert server.get_project_schedule(GUID)["enabled"] is True
        backend.assert_called_once_with("projects/schedule/get", {"projectGuid": GUID})


def test_license_refusal_is_recognized_for_schedule_write():
    session = Mock()
    session.post.return_value = Mock(status_code=403, headers={})
    session.post.return_value.json.return_value = {"code": "SUPPORT_EXPIRED", "writeApplied": False}
    client = ProjectsApiClient(ApiSettings(_env_file=None, url="https://example.test/base/hs/extractor-projects/v1",
                                          user="test", password="test"), session)
    with pytest.raises(ProjectsApiError) as caught:
        client.call("projects/schedule/set", {
            "projectGuid": GUID, "enabled": True, "expectedScheduleState": "state",
        })
    assert caught.value.rejected_before_write


def test_migrate_both_legacy_tables_without_losing_consumed_plans(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    store = OperationStore(path)
    old_plan = store.create_plan("update", {"projectName": "P"}, {}, 300)
    operation = str(uuid.uuid4())
    store.begin(old_plan.plan_id, operation, "update")
    store.complete(operation, {"ok": True})
    with sqlite3.connect(path) as connection:
        for table in ("plans", "operations"):
            schema = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            schema = schema.replace(", 'schedule'", "")
            connection.execute(f"CREATE TEMP TABLE {table}_backup AS SELECT * FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(schema)
            connection.execute(f"INSERT INTO {table} SELECT * FROM {table}_backup")
    migrated = OperationStore(path)
    assert migrated.begin(old_plan.plan_id, operation, "update")[1] == {"ok": True}
    plan = migrated.create_plan("schedule", {"projectGuid": GUID}, {}, 300)
    schedule_operation = str(uuid.uuid4())
    migrated.begin(plan.plan_id, schedule_operation, "schedule")
    migrated.complete(schedule_operation, {"enabled": True})
    reopened = OperationStore(path)
    assert reopened.begin(plan.plan_id, schedule_operation, "schedule")[1] == {"enabled": True}
    with sqlite3.connect(path) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE plans SET action='unknown'")


def test_concurrent_schedule_operation_can_be_started_only_once(tmp_path):
    store = OperationStore(tmp_path / "concurrent.sqlite3")
    plan = store.create_plan("schedule", {"projectGuid": GUID}, {}, 300)
    operation = str(uuid.uuid4())

    def begin():
        try:
            store.begin(plan.plan_id, operation, "schedule")
            return "started"
        except OperationIndeterminate:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: begin(), range(2)))
    assert sorted(results) == ["blocked", "started"]
