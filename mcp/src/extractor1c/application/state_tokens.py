"""Short MCP tokens; native snapshots stay in the MCP/EPA exchange by default."""
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Callable


PREFIX = "mcp-state-v1:"


def short_token(native: str, scope: str, project_name: str) -> str:
    material = json.dumps([scope, project_name.casefold(), native], ensure_ascii=False, separators=(",", ":"))
    return PREFIX + sha256(material.encode("utf-8")).hexdigest()


def resolve_state(payload: dict[str, Any], read: Callable[..., Any], scope: str) -> dict[str, Any]:
    """Resolve only after idempotency replay, retaining EPA's locked comparison."""
    expected = payload.get("expectedState")
    if not isinstance(expected, str) or not expected.startswith(PREFIX):
        return payload  # Existing clients and saved plans keep their native contract.
    if re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{64}", expected) is None:
        raise ValueError("Invalid short state token; no write performed")
    project = payload["projectName"]
    current = read("projects/verify", {"projectName": project})
    native = current.get("stateToken") if isinstance(current, dict) else None
    if not isinstance(native, str) or not native:
        raise ValueError("Native state token unavailable; no write performed")
    if len(native) > 524288:
        raise ValueError("Native state snapshot exceeds the EPA expectedState limit; no write performed")
    if not hmac.compare_digest(expected, short_token(native, scope, project)):
        raise ValueError("Project changed or state token belongs to another base/project; no write performed")
    return {**payload, "expectedState": native}
