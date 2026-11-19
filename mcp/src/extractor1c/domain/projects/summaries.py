"""Компактные проекции ответов 1С: сводка проекта, сводка и поля предпросмотра."""
import json
from typing import Any

from ..paging import list_payload, page


def state_from_verify(value: Any) -> dict[str, Any]:
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


def project_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
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
            **({"rowSettings": state["rowSettings"]} if "rowSettings" in state else {}),
        }]
    return []


def summarize_project(state: dict[str, Any], project_name: str,
                      table_name: str = "") -> dict[str, Any]:
    """Компактная сводка сохранённого проекта без крупного токена состояния."""
    rows = project_rows(state)
    if table_name:
        rows = [row for row in rows if str(row.get("table", "")).casefold() == table_name.casefold()]
    summaries = []
    for row in rows:
        fields = list_payload(row.get("fields", []), "fields", "items")
        guid_fields = [
            {"source": field.get("source"), "target": field.get("target"), "type": field.get("type")}
            for field in fields if isinstance(field, dict) and field.get("type") in {"UUID", "uniqueidentifier"}
        ]
        settings = row.get("sourceSettings") if isinstance(row.get("sourceSettings"), dict) else {}
        summaries.append({
            "source": row.get("source"),
            "table": row.get("table"),
            "disabled": row.get("disabled", False),
            **({"rowSettings": row["rowSettings"]} if "rowSettings" in row else {}),
            "fieldCount": len(fields),
            "guidFields": guid_fields,
            "grouping": settings.get("grouping", []),
            "maxItemsPerBatch": settings.get("maxItemsPerBatch", 0),
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


def preview_summary(value: Any, allow_existing_table: bool = False,
                    repair_missing_table: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"approved": True, "rowCount": 0, "fieldCount": 0}
    if not repair_missing_table and isinstance(value.get("changes"), dict):
        return {
            "approved": value.get("stateMatches") is not False,
            "projectName": value.get("projectName"),
            "stateMatches": value.get("stateMatches"),
            "changes": value["changes"],
            **({"rowSettings": value["rowSettings"]} if "rowSettings" in value else {}),
            "rowCount": 0,
            "fieldCount": 0,
            "rows": [],
        }
    rows = list_payload(value.get("rows", []), "rows", "items")
    if not rows:
        rows = [value]
    field_count = 0
    row_summaries = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        fields = list_payload(row.get("fields", []), "fields", "items")
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
            **({"rowSettings": row["rowSettings"]} if "rowSettings" in row else {}),
        })
    # Изменение существующей строки закономерно находит её проект и таблицу.
    # Разрешение задаётся только типом ProjectRowChange, конфликт состояния сохраняется.
    conflict = bool(value.get("conflict") or value.get("approved") is False
                    or (value.get("projectExists") and not allow_existing_table))
    if value.get("tableExists") and not allow_existing_table:
        conflict = True
    if value.get("stateMatches") is False:
        conflict = True
    if any((row["tableExists"] and not allow_existing_table) or row["projectTableExists"] for row in row_summaries):
        conflict = True
    if repair_missing_table:
        # The saved project row must exist; the physical receiver must not.
        # A partial/old preview cannot authorize a create-absent repair.
        conflict = (value.get("conflict") is not False
                    or value.get("stateMatches") is not True
                    or value.get("projectExists") is not True
                    or value.get("tableExists") is not False
                    or value.get("approved") is False
                    or len(rows) != 1)
        for row in rows:
            if (not isinstance(row, dict) or row.get("tableExists") is not False
                    or row.get("projectTableExists") is not True
                    or row.get("physicalTableExists", False) is not False):
                conflict = True
                continue
            fields = row.get("fields")
            if not isinstance(fields, list) or not fields:
                conflict = True
                continue
            for field in fields:
                if (not isinstance(field, dict)
                        or any(not isinstance(field.get(key), str) or not field[key]
                               for key in ("source", "target", "type"))
                        or any(type(field.get(key)) is not int or field[key] < 0
                               for key in ("precision", "scale"))
                        or any(type(field.get(key)) is not bool for key in (
                            "nullable", "segmentField", "extendedSegmentField", "initializationSegmentField"))):
                    conflict = True
        if value.get("physicalTableExists", False) is not False:
            conflict = True
    result = {
        "approved": not conflict,
        "projectName": value.get("projectName"),
        "rowCount": len(row_summaries),
        "fieldCount": field_count,
        "stateMatches": value.get("stateMatches"),
        "rows": row_summaries,
    }
    if repair_missing_table:
        result.update(receiverRepair=value.get("receiverRepair"), textColumns=value.get("textColumns"))
    if isinstance(value.get("blockers"), list):
        result["blockers"] = value["blockers"]
    return result


def preview_fields_page(value: Any, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
    if not isinstance(value, dict):
        return page([], cursor, limit)
    rows = list_payload(value.get("rows", []), "rows", "items") or [value]
    fields = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        table = row.get("table") or row.get("tableName")
        for field in list_payload(row.get("fields", []), "fields", "items"):
            if isinstance(field, dict):
                fields.append({"table": table, **field})
    return page(fields, cursor, limit)
