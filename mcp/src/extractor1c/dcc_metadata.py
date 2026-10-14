"""Read immutable metadata snapshots through DCC, without contacting 1C."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from .projects_api import ProjectsApiError
from .transport import DccClient

MetadataResource = Literal["snapshot", "objects", "object", "fields", "reference-graph"]


class DccMetadataClient(DccClient):
    """Use fixed browser routes and verify the requested snapshot identity.

    Input: connector UUID, fixed resource and its query/body parameters.
    Output: the DCC data object, including provenance and completeness warnings.
    A missing or expired snapshot is an error; no live HTTP fallback is made.
    """

    def read(
        self,
        base_id: str,
        resource: MetadataResource,
        *,
        snapshot_id: str | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read one browser resource, preserving all fields returned by DCC."""
        if resource not in {"snapshot", "objects", "object", "fields", "reference-graph"}:
            raise ValueError("Unknown metadata browser resource")
        connector_id = str(UUID(base_id))
        path = f"/v2/connectors/{connector_id}/metadata-browser/{resource}"
        query = dict(params or {})
        if snapshot_id is not None:
            query["snapshot_id"] = str(UUID(snapshot_id))
        request_body = dict(body or {})
        if resource == "reference-graph" and snapshot_id is not None:
            request_body["snapshot_id"] = query.pop("snapshot_id")
        envelope = self._request(
            "POST" if resource == "reference-graph" else "GET",
            path,
            headers=self._headers(),
            params=query,
            **({"json": request_body} if resource == "reference-graph" else {}),
        )
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(data, dict):
            raise ProjectsApiError(502, "Invalid DCC metadata response")
        try:
            actual_snapshot = str(UUID(data["snapshotId"]))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ProjectsApiError(502, "Invalid DCC metadata snapshot identity") from None
        if snapshot_id is not None and actual_snapshot != str(UUID(snapshot_id)):
            raise ProjectsApiError(502, "DCC metadata snapshot mismatch")
        if not isinstance(data.get("sourceVersions"), dict) or not isinstance(data.get("warnings"), list):
            raise ProjectsApiError(502, "Invalid DCC metadata provenance")
        if resource in {"objects", "fields"}:
            if not isinstance(data.get("items"), list) or type(data.get("total")) is not int:
                raise ProjectsApiError(502, "Invalid DCC metadata page")
            next_cursor = data.get("nextCursor")
            cursor = query.get("cursor", 0)
            if next_cursor is not None and (type(next_cursor) is not int or next_cursor <= cursor):
                raise ProjectsApiError(502, "DCC metadata pagination did not advance")
        return data
