"""Deterministic client decisions from native evidence; no inferred SQL or joins."""
from copy import deepcopy
import json
from typing import Any

from .projects.payloads import matches_field_mapping_row


def field_choices(value: dict[str, Any], object_name: str) -> dict[str, Any]:
    choices = []
    for field in value.get("items", []):
        if not isinstance(field, dict):
            continue
        names = [part.get("name", "") for part in field.get("types", []) if isinstance(part, dict)]
        types = names or [str(field.get("type", ""))]
        targets = field.get("metadataTargets", [])
        compound = len(types) > 1 or len(targets) > 1
        ambiguous = any("," in name or "|" in name for name in types)
        kind = types[0].casefold() if len(types) == 1 else ""
        options = []
        reason = "TYPE_UNCONFIRMED"
        if compound:
            reason = "COMPOUND_SEGMENT_FORBIDDEN"
        elif ambiguous:
            reason = "TYPE_DESCRIPTION_AMBIGUOUS"
        elif kind in {"дата", "date", "datetime"}:
            options = ["Час", "День", "Неделя", "Месяц", "Квартал", "Полугодие", "Год"]
        elif kind in {"строка", "string"}:
            options = ["По значению", "Первая буква", "Первые две буквы", "Произвольная подстрока"]
        elif kind in {"число", "number", "булево", "boolean"} or (len(targets) == 1 and kind
                and kind not in {"unknown", "any", "неизвестно", "произвольный"}):
            options = ["По значению"]
        if options:
            reason = "SINGLE_TYPE_CONFIRMED"
        choices.append({"field": field.get("name"), "eligible": bool(options), "reason": reason,
                        "exportDecision": "selectable" if options else "native_validation_required",
                        "regularValueBatchSize": (500 if options == ["По значению"] and len(targets) == 1
                                                   and not targets[0].startswith("Перечисление.") else 0),
                        "segments": options,
                        "recommended": ("Месяц" if object_name.endswith(".СрезПоследних") and "Месяц" in options
                                        else "День" if "День" in options else options[0] if options else None)})
    return {**value, "segmentChoices": choices, "choicesComplete": value.get("nextCursor") is None,
            "selectionScope": "metadata_fields_only", "nextAction": "choose_fields_and_segmentation"}


def receiver_decision(value: Any, details: bool = False) -> Any:
    if details:
        return value
    if not isinstance(value, dict) or not isinstance(value.get("checks"), dict):
        return value
    result = deepcopy(value)
    checks = result["checks"]
    blockers = []
    for name, check in checks.items():
        if not isinstance(check, dict) or check.get("status") != "ok":
            blockers.append({"check": name, "code": "CHECK_NOT_OK"})
    schema_check = checks.get("schema", {})
    schema = schema_check.get("result", {}) if isinstance(schema_check, dict) else {}
    schema = schema if isinstance(schema, dict) else {}
    differences = schema.get("mappingDifferences", {})
    differences = differences if isinstance(differences, dict) else {}
    if schema.get("exists") is False:
        blockers.append({"check": "schema", "code": "TABLE_MISSING"})
    for kind in ("missing", "extra"):
        if differences.get(kind):
            blockers.append({"check": "schema", "code": kind.upper() + "_COLUMNS",
                             "fields": differences[kind]})
    if differences.get("typeComparison") in {"different", "mismatch"}:
        blockers.append({"check": "schema", "code": "COLUMN_TYPES_DIFFER"})
    type_differences = []
    unconfirmed = []
    columns = schema.get("columns", [])
    for column in columns if isinstance(columns, list) else []:
        if not isinstance(column, dict):
            unconfirmed.append(None)
            continue
        # Native helper/driver owns types for every receiver. Never parse SQL
        # declarations or reproduce its mapping rules in MCP.
        comparison = column.get("typeComparison", "unknown")
        if comparison in {"different", "mismatch"}:
            type_differences.append({"field": column.get("name"), "expected": column.get("expectedProjectType"),
                                     "precision": column.get("expectedPrecision"), "scale": column.get("expectedScale"),
                                     "actual": column.get("declaredSqlType")})
        elif comparison != "match":
            unconfirmed.append(column.get("name"))
        if column.get("nullabilityComparison") in {"different", "mismatch"}:
            blockers.append({"check": "schema", "code": "NULLABILITY_DIFFERS", "field": column.get("name")})
    if type_differences:
        blockers.append({"check": "schema", "code": "COLUMN_TYPES_DIFFER"})
    schema.pop("columns", None)
    result["verification"] = {
        "status": "differences" if blockers else "observed",
        "blockers": blockers,
        "typesVerified": (differences.get("typeComparison") == "match"
                          or isinstance(columns, list) and bool(columns) and not type_differences and not unconfirmed)
                         and not type_differences
                         and differences.get("typeComparison") not in {"different", "mismatch"},
        "typeVerificationSource": "extractor_1c",
        "typeDifferences": type_differences, "unconfirmedTypes": unconfirmed,
        "sourceEqualityVerified": value.get("sourceEqualityVerified") is True,
        "nextAction": "inspect_differences" if blockers else "choose_next_step",
    }
    return result


def plan_decision(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {"status": "unconfirmed", "blockers": ["INVALID_NATIVE_PREVIEW"], "nextAction": "inspect_details"}
    if (summary.get("nextAction") == "confirm_recreate" and summary.get("tableExists") is True
            and summary.get("stateMatches") is True and summary.get("conflict") is False):
        return {"status": "choice_required", "blockers": ["TABLE_RECREATION_CONFIRMATION_REQUIRED"],
                "nextAction": "confirm_recreate"}
    blockers = []
    if isinstance(summary.get("blockers"), list):
        blockers.extend(summary["blockers"])
    if summary.get("stateMatches") is False:
        blockers.append("PROJECT_CHANGED")
    if summary.get("approved") is False:
        blockers.append("NATIVE_PREVIEW_REFUSED")
    if summary.get("conflict") is True:
        blockers.append("CONFLICT")
    if summary.get("approved") is not True and not blockers:
        blockers.append("APPROVAL_UNCONFIRMED")
    return {"status": "blocked" if blockers else "ready", "blockers": blockers,
            "nextAction": "resolve_blockers" if blockers else "choose_commit_or_cancel"}


def task_decision(value: Any, details: bool = False) -> Any:
    if details:
        return value
    if not isinstance(value, dict):
        return value
    result = dict(value)
    message = value.get("message")
    if isinstance(message, str):
        lines = list(dict.fromkeys(line.strip() for line in message.splitlines() if line.strip()))
        short = "\n".join(lines[:3])[:1000]
        result.update(message=short, messageTruncated=short != message)
    status = value.get("status")
    action = {"succeeded": "inspect_receiver", "failed": "inspect_error",
              "cancelled": "choose_next_step", "running": "wait_same_task",
              "queued": "wait_same_task", "initializing": "wait_same_task", "recovery_required": "reconcile_same_task"}.get(status, "inspect_status")
    result["decision"] = {"status": status, "terminal": status in {"succeeded", "failed", "cancelled"},
                          "nextAction": action, "restartAllowed": False}
    return result


def graph_choices(value: Any, details: bool = False) -> Any:
    if details:
        return value
    if not isinstance(value, dict):
        return value
    choices = []
    for edge in value.get("edges", []):
        if not isinstance(edge, dict):
            continue
        join = edge.get("join", {})
        reason = edge.get("reason")
        if edge.get("status") in {"skipped", "cycle", "identity"}:
            decision = "excluded"
            reason = reason or edge.get("status")
        elif join.get("requiresValidation") or not join.get("sourceField") or not join.get("targetField"):
            decision, reason = "needs_key_validation", "JOIN_KEYS_UNCONFIRMED"
        elif join.get("requiresTypeDiscriminator"):
            decision, reason = "choose_type_branch", "TYPE_DISCRIMINATOR_REQUIRED"
        else:
            decision = "selectable"
        choices.append({"path": edge.get("path"), "target": edge.get("target"),
                        "decision": decision, "reason": reason, "join": join})
    result = {key: item for key, item in value.items() if key not in {"edges", "nodes"}}
    result.update(choices=choices, choicesComplete=value.get("complete") is True and value.get("truncated") is not True,
                  nextAction="choose_reference_paths")
    return result


def _added_field_sources(result: dict[str, Any]) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    changes = result.get("changes")
    if not isinstance(changes, dict) or not isinstance(changes.get("fieldChanges"), list):
        return sources
    for change in changes["fieldChanges"]:
        if not isinstance(change, dict) or "before" not in change or change["before"] is not None:
            continue
        after = change.get("after")
        source = change.get("source")
        if isinstance(after, dict) and isinstance(after.get("target"), str) and isinstance(source, str) and source:
            sources.setdefault(after["target"], []).append(source)
    return sources


def confirm_saved(
    payload: dict[str, Any], saved: dict[str, Any], *, update_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare requested properties to live saved state, never to a requested echo."""
    checked = []
    differences = []
    unconfirmed = []

    def compare(path: str, expected: Any, actual: Any) -> None:
        checked.append(path)
        if json.dumps(expected, sort_keys=True) != json.dumps(actual, sort_keys=True):
            differences.append({"property": path, "expected": expected, "actual": actual})

    for key in ("projectName", "comment", "connectionName"):
        if key in payload and (key != "connectionName" or payload[key]):
            expected, actual = payload[key], saved.get(key)
            if key == "projectName" and isinstance(expected, str) and isinstance(actual, str):
                expected, actual = expected.casefold(), actual.casefold()
            compare(key, expected, actual)
    rows = saved.get("rows") if isinstance(saved.get("rows"), list) else [saved]
    if "receiverChange" in payload:
        table = payload["receiverChange"]["tableName"]
        matches = [row for row in rows if isinstance(row, dict) and row.get("table") == table]
        compare("tableName", table, matches[0].get("table") if len(matches) == 1 else None)
        return {"status": "mismatch" if differences else "partial", "checked": checked,
                "differences": differences, "notIndependentlyChecked": ["settingsPreserved", "receiverSchema"],
                "sourceEqualityVerified": False,
                "nextAction": "resolve_differences" if differences else "inspect_project_receiver"}
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid saved project rows")
    requested = payload.get("rows") if isinstance(payload.get("rows"), list) else [payload.get("definition", payload)]
    for definition in requested:
        if set(definition).issubset({"projectName", "expectedState", "directSql", "comment", "disabled",
                                    "includeGuids", "includeMetadataTypes"}):
            if "disabled" in definition:
                if not rows:
                    raise ValueError("No saved rows to confirm the disabled flag")
                for index, saved_row in enumerate(rows):
                    compare(f"rows[{index}].disabled", definition["disabled"], saved_row.get("disabled"))
            unconfirmed.extend(key for key in ("includeGuids", "includeMetadataTypes") if key in definition)
            continue
        row_keys = {"object", "tableName", "currentTableName", "rowIdentifier", "disabled", "periodicity",
                    "maxItemsPerBatch", "segmentation", "rowSettings", "columnNames", "guidColumnNames",
                    "selectedFields", "fieldChanges", "includeGuids", "includeMetadataTypes", "queryText", "queryParameters"}
        if not row_keys.intersection(definition):
            continue  # A project-property update needs no arbitrary row selection.
        table = definition.get("currentTableName", definition.get("tableName"))
        identifier = definition.get("rowIdentifier")
        field_result = (
            update_result if update_result and definition.get("fieldChanges")
            and matches_field_mapping_row(definition, update_result) else {}
        )
        matches = [row for row in rows if (
            str(row.get("rowIdentifier")) == str(identifier) if identifier is not None
            else table is not None and row.get("table") == table)]
        if not matches and identifier is not None:
            confirmed_table = field_result.get("currentTableName")
            if isinstance(confirmed_table, str) and confirmed_table:
                same_table = [row for row in rows if row.get("table") == confirmed_table]
                if len(same_table) == 1 and same_table[0].get("rowIdentifier") is None:
                    matches = same_table
        row = matches[0] if len(matches) == 1 else rows[0] if table is None and identifier is None and len(rows) == 1 else None
        if row is None:
            differences.append({"property": "row", "expected": table, "actual": "not uniquely found"})
            continue
        for key, actual_key in (("object", "source"), ("tableName", "table"), ("disabled", "disabled")):
            if key in definition:
                compare(key, definition[key], row.get(actual_key))
        settings = row.get("sourceSettings", {})
        if not isinstance(settings, dict):
            raise ValueError("Invalid saved source settings")
        for key in ("periodicity", "maxItemsPerBatch"):
            if key in definition:
                compare(key, definition[key], settings.get(key))
        if "segmentation" in definition:
            if any(not isinstance(group, dict) for group in settings.get("grouping", [])):
                raise ValueError("Invalid saved grouping")
            expected = sorted((g["field"], g["segment"], g["regular"], g["extended"], g["initialization"])
                              for g in definition["segmentation"])
            actual = sorted(((g.get("path"), g.get("function") or "По значению", g.get("regular"),
                             g.get("extended"), g.get("initialization"))
                            for g in settings.get("grouping", []) if g.get("enabled") is True), key=repr)
            expected = sorted(expected, key=repr)
            compare("segmentation", expected, actual)
        for key, expected in definition.get("rowSettings", {}).items():
            compare("rowSettings." + key, expected, row.get("rowSettings", {}).get("saved", {}).get(key))
        fields = row.get("fields", [])
        if not isinstance(fields, list) or any(not isinstance(field, dict) for field in fields):
            raise ValueError("Invalid saved fields")
        for key in ("columnNames", "guidColumnNames"):
            for source, target in definition.get(key, {}).items():
                mapping = [f for f in fields if f.get("source") == source]
                compare(key + "." + source, target, mapping[0].get("target") if len(mapping) == 1 else None)
        for source in definition.get("selectedFields", []):
            mapping = [f for f in fields if f.get("source") == source]
            compare("selectedFields." + source, True, len(mapping) == 1 and mapping[0].get("noExport") is False)
        added_sources = _added_field_sources(field_result)
        for change in definition.get("fieldChanges", []):
            source = change["source"]
            aliases = added_sources.get(change.get("target"), []) if change.get("add") and "." in source else []
            mapping_source = aliases[0] if len(aliases) == 1 else source
            mapping = [field for field in fields if field.get("source") == mapping_source]
            if len(mapping) != 1:
                differences.append({"property": "fieldChanges", "expected": change["source"],
                                    "actual": "not uniquely found"})
                continue
            for key in ("target", "noExport"):
                if key in change:
                    compare(change["source"] + "." + key, change[key], mapping[0].get(key))
            if change.get("add"):
                compare(source + ".noExport", False, mapping[0].get("noExport"))
        for key in ("includeGuids", "includeMetadataTypes", "queryText", "queryParameters"):
            if key in definition:
                unconfirmed.append(key)
    status = "mismatch" if differences else "partial" if unconfirmed else "matched"
    return {"status": status, "checked": checked, "differences": differences,
            "notIndependentlyChecked": sorted(set(unconfirmed)), "sourceEqualityVerified": False,
            "nextAction": "resolve_differences" if differences else "choose_initialization_or_export"}
