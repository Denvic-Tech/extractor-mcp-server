"""Compact projections and metadata analysis for hosted MCP clients."""
from __future__ import annotations

import json
import re
from typing import Any, Callable


CallApi = Callable[[str, dict[str, Any]], Any]


def _list_payload(value: Any, *keys: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            items = value.get(key)
            if isinstance(items, list):
                return items
    return []


def page(items: list[Any], cursor: int, limit: int) -> dict[str, Any]:
    if cursor < 0:
        raise ValueError("cursor must be nonnegative")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    selected = items[cursor:cursor + limit]
    next_cursor = cursor + len(selected)
    return {
        "items": selected,
        "total": len(items),
        "nextCursor": next_cursor if next_cursor < len(items) else None,
    }


def search_metadata(call_api: CallApi, query: str = "", kinds: list[str] | None = None,
                    cursor: int = 0, limit: int = 50) -> dict[str, Any]:
    if len(query) > 200:
        raise ValueError("query is too long")
    allowed_kinds = set(kinds or [])
    if len(allowed_kinds) > 20 or any(
        not isinstance(kind, str) or not re.fullmatch(r"[A-Za-zА-Яа-яЁё]+", kind)
        for kind in allowed_kinds
    ):
        raise ValueError("invalid metadata kinds")
    values = _list_payload(call_api("metadata/list", {}), "objects", "items")
    names = sorted(str(value) for value in values)
    folded = query.casefold().strip()
    filtered = [
        name for name in names
        if (not folded or folded in name.casefold())
        and (not allowed_kinds or name.partition(".")[0] in allowed_kinds)
    ]
    return page(filtered, cursor, limit)


def describe_object_page(call_api: CallApi, object_name: str, name_filter: str = "",
                         reference_only: bool = False, cursor: int = 0,
                         limit: int = 100) -> dict[str, Any]:
    if len(name_filter) > 200:
        raise ValueError("nameFilter is too long")
    fields = _list_payload(call_api("metadata/describe", {"object": object_name}), "fields", "items")
    folded = name_filter.casefold().strip()
    filtered = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        field = {
            key: raw.get(key) for key in ("name", "type", "kind", "metadataTargets")
            if key in raw
        }
        if folded and folded not in str(field.get("name", "")).casefold():
            continue
        targets = field.get("metadataTargets")
        if reference_only and not targets:
            continue
        filtered.append(field)
    result = page(filtered, cursor, limit)
    result["object"] = object_name
    source_fields = [field for field in fields if isinstance(field, dict)]
    result["metadataEnriched"] = (
        all("metadataTargets" in field for field in source_fields) if source_fields else True
    )
    return result


def _state_from_verify(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    token = value.get("stateToken")
    if isinstance(token, str):
        try:
            decoded = json.loads(token)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    return value


def _project_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = state.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    if state.get("source") or state.get("table"):
        return [{
            "source": state.get("source"),
            "table": state.get("table"),
            "disabled": state.get("disabled"),
            "fields": state.get("fields", []),
            "sourceSettings": state.get("sourceSettings", {}),
        }]
    return []


def verify_project_summary(call_api: CallApi, project_name: str,
                           table_name: str = "") -> dict[str, Any]:
    state = _state_from_verify(call_api("projects/verify", {"projectName": project_name}))
    rows = _project_rows(state)
    if table_name:
        rows = [row for row in rows if str(row.get("table", "")).casefold() == table_name.casefold()]
    summaries = []
    for row in rows:
        fields = _list_payload(row.get("fields", []), "fields", "items")
        guid_fields = [
            {"source": field.get("source"), "target": field.get("target"), "type": field.get("type")}
            for field in fields if isinstance(field, dict) and field.get("type") in {"UUID", "uniqueidentifier"}
        ]
        settings = row.get("sourceSettings") if isinstance(row.get("sourceSettings"), dict) else {}
        summaries.append({
            "source": row.get("source"),
            "table": row.get("table"),
            "disabled": row.get("disabled", False),
            "fieldCount": len(fields),
            "guidFields": guid_fields,
            "grouping": settings.get("grouping", []),
            "maxItemsPerBatch": settings.get("maxItemsPerBatch", 0),
            # //(Денис 2026-10-03. Версия MCP 1.2.0
            # Группировки не задают роль: читаем отдельные флаги маппинга; старый API явно отмечаем.
            "segmentation": {
                "extendedMode": settings.get("extendedMode"),
                "rolesAvailable": bool(fields) and all(
                    "extendedSegmentField" in field and "initializationSegmentField" in field
                    for field in fields if isinstance(field, dict)
                ),
                "regular": [field.get("source") for field in fields if isinstance(field, dict) and field.get("segmentField") is True],
                "extended": [field.get("source") for field in fields if isinstance(field, dict) and field.get("extendedSegmentField") is True],
                "initialization": [field.get("source") for field in fields if isinstance(field, dict) and field.get("initializationSegmentField") is True],
            },
            # \\\Денис 2026-10-03. Версия MCP 1.2.0)
        })
    return {
        "projectName": state.get("projectName", project_name),
        "projectRef": state.get("projectRef"),
        "connectionName": state.get("connectionName"),
        "comment": state.get("comment", ""),
        "rowCount": len(summaries),
        "fieldsMapped": state.get("fieldsMapped"),
        "rows": summaries,
    }


def preview_summary(value: Any, allow_existing_table: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"approved": True, "rowCount": 0, "fieldCount": 0}
    if isinstance(value.get("changes"), dict):
        return {
            "approved": value.get("stateMatches") is not False,
            "projectName": value.get("projectName"),
            "stateMatches": value.get("stateMatches"),
            "changes": value["changes"],
            "rowCount": 0,
            "fieldCount": 0,
            "rows": [],
        }
    rows = _list_payload(value.get("rows", []), "rows", "items")
    if not rows:
        rows = [value]
    field_count = 0
    row_summaries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = _list_payload(row.get("fields", []), "fields", "items")
        field_count += len(fields)
        settings = row.get("sourceSettings") if isinstance(row.get("sourceSettings"), dict) else {}
        compact_settings = {key: settings[key] for key in ("grouping", "maxItemsPerBatch", "extendedMode") if key in settings}
        # 1C serializes an absent zero-limit row attribute as null in previews.
        if "maxItemsPerBatch" in compact_settings and compact_settings["maxItemsPerBatch"] is None:
            compact_settings["maxItemsPerBatch"] = 0
        row_summaries.append({
            "object": row.get("object"),
            "table": row.get("table") or row.get("tableName"),
            "fieldCount": len(fields),
            "tableExists": bool(row.get("tableExists")),
            "projectTableExists": bool(row.get("projectTableExists")),
            "sourceSettings": compact_settings,
        })
    # //(Денис 2026-10-03. Версия MCP 1.2.0
    # Изменение существующей строки закономерно находит её проект и таблицу.
    # Разрешение задаётся только типом ProjectRowChange, конфликт состояния сохраняется.
    conflict = bool(value.get("conflict") or (value.get("projectExists") and not allow_existing_table))
    # \\\Денис 2026-10-03. Версия MCP 1.2.0)
    if value.get("tableExists") and not allow_existing_table:
        conflict = True
    if value.get("stateMatches") is False:
        conflict = True
    if any((row["tableExists"] and not allow_existing_table) or row["projectTableExists"] for row in row_summaries):
        conflict = True
    return {
        "approved": not conflict,
        "projectName": value.get("projectName"),
        "rowCount": len(row_summaries),
        "fieldCount": field_count,
        "stateMatches": value.get("stateMatches"),
        "rows": row_summaries,
    }


def preview_fields_page(value: Any, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
    if not isinstance(value, dict):
        return page([], cursor, limit)
    rows = _list_payload(value.get("rows", []), "rows", "items") or [value]
    fields = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        table = row.get("table") or row.get("tableName")
        for field in _list_payload(row.get("fields", []), "fields", "items"):
            if isinstance(field, dict):
                fields.append({"table": table, **field})
    return page(fields, cursor, limit)
