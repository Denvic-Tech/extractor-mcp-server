"""Durable preview plans and idempotency records for MCP write operations."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Any, Literal


class OperationStoreError(RuntimeError):
    """Base error for safe plan/operation handling."""


class PlanNotFound(OperationStoreError):
    pass


class PlanExpired(OperationStoreError):
    pass


class PlanConflict(OperationStoreError):
    pass


class OperationIndeterminate(OperationStoreError):
    pass


class OperationRejected(OperationStoreError):
    def __init__(self, result):
        self.result = result
        super().__init__(f"{result['code']}: {result['error']} Запись не выполнялась. Получите новый preview перед новой попыткой.")


@dataclass(frozen=True)
class Plan:
    plan_id: str
    action: Literal["create", "update", "schedule", "export", "initialization"]
    payload: dict[str, Any]
    payload_hash: str
    preview: Any
    created_at: float
    expires_at: float
    operation_id: str | None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise OperationStoreError(f"{label} must be a UUID") from exc
    return str(parsed)


class OperationStore:
    """SQLite-backed store; the configured path must live on persistent storage."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'schedule', 'export', 'initialization')),
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    preview_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    operation_id TEXT
                );
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'schedule', 'export', 'initialization')),
                    payload_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'indeterminate', 'rejected')),
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
                );
                CREATE INDEX IF NOT EXISTS operations_plan_id ON operations(plan_id);
                """
            )
            # Rebuild both constrained tables in one transaction, preserving all
            # consumed plans and operations. FK checks run before committing.
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            for table in ("plans", "operations"):
                schema = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
                ).fetchone()[0]
                if all(repr(action) in schema for action in ("schedule", "export", "initialization")) and (table == "plans" or "'rejected'" in schema):
                    continue
                upgraded = schema.replace(
                    "'create', 'update'", "'create', 'update', 'schedule'"
                ) if "'schedule'" not in schema else schema
                if "'export'" not in upgraded:
                    upgraded = upgraded.replace("'schedule'", "'schedule', 'export'")
                if "'initialization'" not in upgraded:
                    upgraded = upgraded.replace("'export'", "'export', 'initialization'")
                if table == "operations" and "'rejected'" not in schema:
                    upgraded = upgraded.replace("'indeterminate'", "'indeterminate', 'rejected'")
                # Names here are a fixed internal allowlist, never user input.
                columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
                column_names = ", ".join('"' + row["name"] + '"' for row in columns)
                connection.execute(f"CREATE TEMP TABLE {table}_backup AS SELECT * FROM {table}")
                connection.execute(f"DROP TABLE {table}")
                connection.execute(upgraded)
                connection.execute(
                    f"INSERT INTO {table} ({column_names}) SELECT {column_names} FROM {table}_backup"
                )
                connection.execute(f"DROP TABLE {table}_backup")
            connection.execute("CREATE INDEX IF NOT EXISTS operations_plan_id ON operations(plan_id)")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise OperationStoreError("Operation store migration failed foreign key validation")
            connection.commit()
            connection.execute("PRAGMA foreign_keys = ON")

    def ready(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1

    def create_plan(self, action: Literal["create", "update", "schedule", "export", "initialization"], payload: dict[str, Any],
                    preview: Any, ttl_seconds: int) -> Plan:
        now = time.time()
        plan = Plan(
            plan_id=str(uuid.uuid4()),
            action=action,
            payload=payload,
            payload_hash=payload_hash(payload),
            preview=preview,
            created_at=now,
            expires_at=now + ttl_seconds,
            operation_id=None,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (plan.plan_id, plan.action, canonical_json(plan.payload), plan.payload_hash,
                 canonical_json(plan.preview), plan.created_at, plan.expires_at),
            )
        return plan

    def get_plan(self, plan_id: str) -> Plan:
        plan_id = validate_uuid(plan_id, "planId")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise PlanNotFound("Plan not found")
        plan = Plan(
            plan_id=row["plan_id"], action=row["action"],
            payload=json.loads(row["payload_json"]), payload_hash=row["payload_hash"],
            preview=json.loads(row["preview_json"]), created_at=row["created_at"],
            expires_at=row["expires_at"], operation_id=row["operation_id"],
        )
        if plan.expires_at < time.time() and plan.operation_id is None:
            raise PlanExpired("Plan expired; run preview again")
        return plan

    def begin(self, plan_id: str, operation_id: str,
              expected_action: Literal["create", "update", "schedule", "export", "initialization"]) -> tuple[Plan, Any | None]:
        plan_id = validate_uuid(plan_id, "planId")
        operation_id = validate_uuid(operation_id, "operationId")
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise PlanNotFound("Plan not found")
            plan = Plan(
                plan_id=row["plan_id"], action=row["action"],
                payload=json.loads(row["payload_json"]), payload_hash=row["payload_hash"],
                preview=json.loads(row["preview_json"]), created_at=row["created_at"],
                expires_at=row["expires_at"], operation_id=row["operation_id"],
            )
            if plan.action != expected_action:
                connection.rollback()
                raise PlanConflict("Plan action does not match the requested operation")
            existing = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if existing is not None:
                if (existing["plan_id"] != plan_id or existing["action"] != expected_action
                        or existing["payload_hash"] != plan.payload_hash):
                    connection.rollback()
                    raise PlanConflict("operationId was already used for another payload")
                if existing["status"] == "completed":
                    result = json.loads(existing["result_json"])
                    connection.commit()
                    return plan, result
                if existing["status"] == "rejected":
                    result = json.loads(existing["result_json"])
                    connection.rollback()
                    raise OperationRejected(result)
                connection.rollback()
                raise OperationIndeterminate(
                    "Operation outcome is indeterminate; verify project state before any new write"
                )
            if plan.operation_id is not None:
                connection.rollback()
                raise PlanConflict("Plan was already consumed by another operation")
            if plan.expires_at < now:
                connection.rollback()
                raise PlanExpired("Plan expired; run preview again")
            if expected_action in {"export", "initialization"}:
                # A fresh plan/operation must not bypass an uncertain launch.
                # BEGIN IMMEDIATE serializes this check with competing starts.
                pending = connection.execute(
                    "SELECT p.payload_json FROM operations o JOIN plans p ON p.plan_id=o.plan_id "
                    "WHERE o.action IN ('export', 'initialization') AND o.status IN ('in_progress', 'indeterminate')"
                ).fetchall()
                project = plan.payload["projectGuid"].casefold()
                if any(json.loads(row[0])["projectGuid"].casefold() == project for row in pending):
                    connection.rollback()
                    raise OperationIndeterminate(
                        "An unresolved export or initialization launch exists for this project; reconcile it before another start"
                    )
            connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, 'in_progress', NULL, ?, ?)",
                (operation_id, plan_id, expected_action, plan.payload_hash, now, now),
            )
            connection.execute(
                "UPDATE plans SET operation_id = ? WHERE plan_id = ?",
                (operation_id, plan_id),
            )
            connection.commit()
        return plan, None

    def complete(self, operation_id: str, result: Any) -> None:
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET status = 'completed', result_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND status = 'in_progress'",
                (canonical_json(result), time.time(), operation_id),
            )

    def mark_indeterminate(self, operation_id: str, result: Any = None) -> None:
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET status = 'indeterminate', result_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND status = 'in_progress'",
                (canonical_json(result) if result is not None else None, time.time(), operation_id),
            )

    def reject(self, operation_id: str, result: dict[str, Any]) -> None:
        """Persist a confirmed pre-write refusal; the consumed plan remains immutable."""
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET status = 'rejected', result_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND status = 'in_progress'",
                (canonical_json(result), time.time(), operation_id),
            )

    def operation_status(self, operation_id: str) -> dict[str, Any]:
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT operation_id, plan_id, action, status, result_json, created_at, updated_at "
                "FROM operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            raise OperationStoreError("Operation not found")
        return {
            "operationId": row["operation_id"],
            "planId": row["plan_id"],
            "action": row["action"],
            "status": row["status"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def remember_relay(self, operation_id: str, route: str) -> None:
        """Bind a pending read to this already partitioned caller/base store."""
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS relay_reads (operation_id TEXT PRIMARY KEY, route TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO relay_reads VALUES (?, ?)", (operation_id, route))

    def relay_route(self, operation_id: str) -> str:
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS relay_reads (operation_id TEXT PRIMARY KEY, route TEXT NOT NULL)")
            row = connection.execute("SELECT route FROM relay_reads WHERE operation_id = ?", (operation_id,)).fetchone()
        if row is None:
            raise OperationStoreError("Operation not found")
        return row["route"]

    def reconcile(self, operation_id: str, result: Any, *, rejected: bool = False) -> None:
        """Resolve an uncertain relay request only from its matching upstream record."""
        operation_id = validate_uuid(operation_id, "operationId")
        with self._connect() as connection:
            connection.execute(
                "UPDATE operations SET status = ?, result_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND status IN ('in_progress', 'indeterminate')",
                ("rejected" if rejected else "completed", canonical_json(result), time.time(), operation_id),
            )
