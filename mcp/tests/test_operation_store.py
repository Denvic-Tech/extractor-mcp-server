import sqlite3
import uuid

import pytest

from extractor1c.operation_store import OperationIndeterminate, OperationStore, PlanConflict


def test_plan_is_single_use_and_completed_result_is_replayable(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    assert store.ready() is True
    plan = store.create_plan("create", {"projectName": "P"}, {"approved": True}, 300)
    operation_id = str(uuid.uuid4())
    started, replay = store.begin(plan.plan_id, operation_id, "create")
    assert started.payload == {"projectName": "P"}
    assert replay is None
    store.complete(operation_id, {"ok": True})
    _, replay = store.begin(plan.plan_id, operation_id, "create")
    assert replay == {"ok": True}
    with pytest.raises(PlanConflict):
        store.begin(plan.plan_id, str(uuid.uuid4()), "create")


def test_uncertain_write_cannot_be_retried_blindly(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    plan = store.create_plan("update", {"projectName": "P"}, {}, 300)
    operation_id = str(uuid.uuid4())
    store.begin(plan.plan_id, operation_id, "update")
    store.mark_indeterminate(operation_id)
    with pytest.raises(OperationIndeterminate):
        store.begin(plan.plan_id, operation_id, "update")
    assert store.operation_status(operation_id)["status"] == "indeterminate"


def test_relay_task_binding_survives_restart_and_rejects_replacement(tmp_path):
    path = tmp_path / "relay.sqlite3"
    operation_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    store = OperationStore(path)
    store.remember_relay(operation_id, "projects/list", task_id)
    reopened = OperationStore(path)
    reopened.validate_relay_task(operation_id, "projects/list", task_id)
    with pytest.raises(PlanConflict, match="identity changed"):
        reopened.validate_relay_task(operation_id, "projects/list", str(uuid.uuid4()))
    with pytest.raises(PlanConflict, match="identity changed"):
        reopened.remember_relay(operation_id, "metadata/list", task_id)
    reopened.validate_relay_task(operation_id, "projects/list", task_id)


def test_legacy_pending_reads_migrate_without_losing_routes(tmp_path):
    path = tmp_path / "old-relay.sqlite3"
    operation_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE relay_reads (operation_id TEXT PRIMARY KEY, route TEXT NOT NULL)")
        connection.execute("INSERT INTO relay_reads VALUES (?, ?)", (operation_id, "projects/list"))
    store = OperationStore(path)
    assert store.relay_route(operation_id) == "projects/list"
    store.validate_relay_task(operation_id, "projects/list", task_id)
    with pytest.raises(PlanConflict):
        OperationStore(path).validate_relay_task(operation_id, "projects/list", str(uuid.uuid4()))


def test_existing_store_migration_preserves_operations_and_constraints(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    store = OperationStore(path)
    plan = store.create_plan("create", {"projectName": "P"}, {}, 300)
    operation_id = str(uuid.uuid4())
    store.begin(plan.plan_id, operation_id, "create")
    store.complete(operation_id, {"ok": True})
    with sqlite3.connect(path) as connection:
        # Recreate exactly the old CHECK constraint with existing persisted data.
        connection.executescript("""
            CREATE TABLE operations_old (
                operation_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('create', 'update')),
                payload_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'indeterminate')),
                result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
            );
            INSERT INTO operations_old SELECT * FROM operations;
            DROP TABLE operations;
            ALTER TABLE operations_old RENAME TO operations;
            CREATE INDEX operations_plan_id ON operations(plan_id);
        """)
    migrated = OperationStore(path)
    assert migrated.begin(plan.plan_id, operation_id, "create")[1] == {"ok": True}
    another_plan = migrated.create_plan("update", {"projectName": "P"}, {}, 300)
    rejected_id = str(uuid.uuid4())
    migrated.begin(another_plan.plan_id, rejected_id, "update")
    migrated.reject(rejected_id, {"code": "SUPPORT_EXPIRED", "error": "Expired", "writeApplied": False})
    reopened = OperationStore(path)
    assert reopened.operation_status(rejected_id)["status"] == "rejected"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='operations_plan_id'").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE operations SET status = 'invalid'")
