"""План изменения и неизменяемая идентичность операции записи."""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Literal
import uuid

from .errors import OperationStoreError

Action = Literal["create", "update", "schedule", "export", "initialization", "cancel"]

# Прямая SQL-операция хранится тем же журналом; предпросмотра у неё не было.
DIRECT_ACTIONS = frozenset({"create", "update", "export", "initialization"})
DIRECT_SQL_PREVIEW = {"executionMode": "direct_sql", "previewPerformed": False}


@dataclass(frozen=True)
class Plan:
    plan_id: str
    action: Action
    payload: dict[str, Any]
    payload_hash: str
    preview: Any
    created_at: float
    expires_at: float
    operation_id: str | None

    @property
    def is_direct(self) -> bool:
        return isinstance(self.preview, dict) and self.preview.get("executionMode") == "direct_sql"


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
