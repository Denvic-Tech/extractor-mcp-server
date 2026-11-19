"""Предпросмотр создания и изменения проекта.

Предпросмотр ничего не меняет в 1С. Одобренный результат сохраняется как план:
точная полезная нагрузка, которую позже выполнит запись по ``planId``.
У каждого вида изменения свои правила сверки ответа 1С, поэтому сценарий
разделён на три ветви: сопоставление полей, настройки строки и изменения,
которым нужен приёмник.
"""
from typing import Any

from ..domain.identifiers import is_uuid
from ..domain.projects.changes import (
    ProjectFieldMappingChange, ProjectPreview, ProjectReceiverRepair, ProjectRowChange, ProjectRowSettingsChange,
    ProjectUpdate,
)
from ..domain.projects.definition import GroupProjectDefinition, ProjectDefinition, RowSettings
from ..domain.projects.payloads import matches_field_mapping_row, uses_column_names, uses_query_source
from ..domain.projects.summaries import preview_summary
from .capabilities import require_capabilities
from .connections import resolve_project_connection
from .errors import ApplicationError
from .ports import AppContext
from .state_tokens import PREFIX, resolve_state, short_token


def preview_project_definition(ctx: AppContext, definition: ProjectPreview,
                               metadata_snapshot_id: str | None = None) -> Any:
    """Resolve receiver and store the exact approved native preview input once."""
    raw_payload = definition.model_dump(exclude_none=True)
    if uses_query_source(raw_payload):
        require_capabilities(ctx.call, "query_sources")
    if uses_column_names(raw_payload):
        # Старая EPA должна быть отклонена до изменяющего вызова, а не после него.
        require_capabilities(ctx.call, "column_names")
    if isinstance(definition, ProjectFieldMappingChange):
        return _preview_field_mapping(ctx, definition, raw_payload)
    rows = raw_payload.get("rows", [raw_payload.get("definition", raw_payload)])
    uses_row_settings = isinstance(definition, ProjectRowSettingsChange) or any(
        "rowSettings" in row for row in rows
    )
    if uses_row_settings:
        require_capabilities(ctx.call, "row_settings")
    if isinstance(definition, ProjectRowSettingsChange):
        return _preview_row_settings(ctx, definition, raw_payload)
    return _preview_with_receiver(ctx, definition, raw_payload, rows, uses_row_settings, metadata_snapshot_id)


def _preview_field_mapping(ctx: AppContext, definition: ProjectFieldMappingChange,
                           raw_payload: dict[str, Any]) -> dict[str, Any]:
    # A saved-field patch is already bound to its receiver. Never rebuild
    # the helper definition or add connection overrides to this contract.
    require_capabilities(ctx.call, "field_mapping",
                         *(("field_selection",) if "rowIdentifier" in raw_payload else ()))
    preview = ctx.call("projects/preview", _resolve(ctx, raw_payload))
    if (not isinstance(preview, dict)
            or preview.get("projectName") != definition.projectName
            or not matches_field_mapping_row(raw_payload, preview)
            or preview.get("requested") != raw_payload["fieldChanges"]):
        raise ApplicationError("Invalid native field mapping preview; no plan was created")
    summary = preview_summary(preview)
    summary["approved"] = (preview.get("stateMatches") is True and preview.get("conflict") is False
                           and preview.get("approved") is not False)
    if not summary["approved"]:
        return {"approved": False, "action": "update", "summary": summary, "planId": None}
    plan = ctx.operations.create_plan("update", raw_payload, preview, ctx.plan_ttl_seconds)
    return {"approved": True, "action": "update", "summary": summary,
            "planId": plan.plan_id, "expiresAt": plan.expires_at}


def _preview_row_settings(ctx: AppContext, definition: ProjectRowSettingsChange,
                          raw_payload: dict[str, Any]) -> dict[str, Any]:
    # This patch only changes saved row attributes. Do not discover or touch
    # a receiver, rebuild mappings, or inject connection overrides.
    preview = ctx.call("projects/preview", _resolve(ctx, raw_payload))
    if not isinstance(preview, dict):
        raise ApplicationError("Invalid native row settings preview; no plan was created")
    summary = preview_summary(preview)
    if (preview.get("stateMatches") is not True or preview.get("conflict") is not False
            or preview.get("approved") is False):
        summary["approved"] = False
        return {"approved": False, "action": "update", "summary": summary,
                "planId": None, "expiresAt": None}
    changes = preview.get("changes")
    change = changes.get("rowSettings", {}) if isinstance(changes, dict) else {}
    requested = raw_payload["rowSettings"]
    identity_valid = (
        preview.get("projectName") == definition.projectName
        and preview.get("currentTableName") == definition.currentTableName
        and is_uuid(preview.get("projectRef"))
        and isinstance(preview.get("rowIdentifier"), str) and bool(preview["rowIdentifier"].strip())
        and isinstance(preview.get("connectionName"), str)
    )
    try:
        echoed = RowSettings.model_validate(change["requested"]).model_dump()
        before = RowSettings.model_validate(change["before"]).model_dump()
        after = RowSettings.model_validate(change["after"]).model_dump()
        saved = RowSettings.model_validate(preview["rowSettings"]["saved"]).model_dump()
        settings_valid = (
            set(before) == set(RowSettings.model_fields)
            and set(after) == set(RowSettings.model_fields)
            and echoed == requested and saved == after
            and all(after[key] == value and type(after[key]) is type(value)
                    for key, value in requested.items())
            and all(after[key] == value for key, value in before.items() if key not in requested)
        )
    except (KeyError, TypeError, ValueError):
        settings_valid = False
    if not identity_valid or not settings_valid:
        raise ApplicationError("Invalid native row settings identity or changes; no plan was created")
    plan = ctx.operations.create_plan("update", raw_payload, preview, ctx.plan_ttl_seconds)
    return {"approved": True, "action": "update", "summary": summary,
            "connectionSelection": {"status": "selected", "connectionName": preview["connectionName"],
                                    "choices": [], "source": "saved_project"},
            "planId": plan.plan_id, "expiresAt": plan.expires_at}


def _preview_with_receiver(ctx: AppContext, definition: ProjectPreview, raw_payload: dict[str, Any],
                           rows: list[dict[str, Any]], uses_row_settings: bool,
                           metadata_snapshot_id: str | None) -> dict[str, Any]:
    repair = isinstance(definition, ProjectReceiverRepair)
    if repair:
        require_capabilities(ctx.call, "mysql_receiver_repair")
    metadata_evidence = (ctx.read_snapshot("snapshot", metadata_snapshot_id)
                         if metadata_snapshot_id is not None else None)
    try:
        payload, selection = resolve_project_connection(raw_payload, ctx.call)
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None
    if selection["status"] != "selected":
        return {"approved": False, "planId": None, "expiresAt": None,
                "connectionSelection": selection}
    if repair and not any(choice["name"] == selection["connectionName"]
                          and choice.get("type", "").casefold() == "mysql"
                          for choice in selection["choices"]):
        raise ApplicationError("Receiver repair requires the saved MySQL connection")
    preview = ctx.call("projects/preview", _resolve(ctx, payload))
    if repair and (not isinstance(preview, dict)
                   or preview.get("receiverRepair") != definition.receiverRepair
                   or preview.get("textColumns") != definition.textColumns
                   or preview.get("table") != definition.currentTableName):
        raise ApplicationError("Invalid receiver repair preview; no plan was created")
    if uses_row_settings:
        _require_row_settings_readback(preview, rows)
    if metadata_evidence is not None:
        # Provenance belongs to the approved plan, never to the 1C business payload.
        preview = {**preview, "metadataEvidence": metadata_evidence}
    summary = preview_summary(
        preview, allow_existing_table=isinstance(definition, (ProjectRowChange, ProjectReceiverRepair)),
        repair_missing_table=repair,
    )
    action = "create" if isinstance(definition, (ProjectDefinition, GroupProjectDefinition)) else "update"
    summary["connectionName"] = selection["connectionName"]
    result = {"approved": summary["approved"], "action": action, "summary": summary,
              "connectionSelection": selection}
    if metadata_evidence is not None:
        result["metadataEvidence"] = metadata_evidence
    if summary["approved"]:
        plan = ctx.operations.create_plan(action, payload, preview, ctx.plan_ttl_seconds)
        result.update({"planId": plan.plan_id, "expiresAt": plan.expires_at})
    else:
        result.update({"planId": None, "expiresAt": None})
    return result


def _require_row_settings_readback(preview: Any, rows: list[dict[str, Any]]) -> None:
    """1С должна вернуть ровно те настройки строки, которые запросил пользователь."""
    preview_rows = preview.get("rows", [preview]) if isinstance(preview, dict) else []
    if not isinstance(preview_rows, list):
        raise ApplicationError("Invalid native row settings readback; no plan was created")
    for requested_row in rows:
        requested_settings = requested_row.get("rowSettings")
        if requested_settings is None:
            continue
        matching = [row for row in preview_rows if isinstance(row, dict)
                    and (row.get("table") or row.get("tableName")) == requested_row["tableName"]]
        try:
            if len(matching) != 1:
                raise ValueError("Missing unique row settings preview")
            native_row = matching[0]
            saved = RowSettings.model_validate(native_row["rowSettings"]["saved"]).model_dump()
            if (set(saved) != set(RowSettings.model_fields)
                    or any(saved[key] != value or type(saved[key]) is not type(value)
                           for key, value in requested_settings.items())):
                raise ValueError("Native row settings did not match the request")
            batch = native_row["sourceSettings"]["maxItemsPerBatch"]
            if ((batch is not None and type(batch) is not int)
                    or (0 if batch is None else batch) != saved["queueBatchSize"]):
                raise ValueError("Native queue batch alias is inconsistent")
        except (KeyError, TypeError, ValueError):
            raise ApplicationError("Invalid native row settings readback; no plan was created") from None


def preview_project_properties(ctx: AppContext, update: ProjectUpdate) -> dict[str, Any]:
    """Привязать изменение свойств проекта к проверенному состоянию."""
    verified = ctx.call("projects/verify", {"projectName": update.projectName})
    native = verified.get("stateToken") if isinstance(verified, dict) else None
    state_matches = isinstance(native, str) and (native == update.expectedState or (
        update.expectedState.startswith(PREFIX) and short_token(native, ctx.state_scope, update.projectName) == update.expectedState))
    preview = {
        "projectName": update.projectName,
        "stateMatches": state_matches,
        "changes": {key: value for key, value in update.model_dump().items()
                    if key not in {"projectName", "expectedState"}},
    }
    if not state_matches:
        return {"approved": False, "action": "update", "planId": None,
                "expiresAt": None, "summary": preview}
    plan = ctx.operations.create_plan("update", update.model_dump(), preview, ctx.plan_ttl_seconds)
    return {"approved": True, "action": "update", "planId": plan.plan_id,
            "expiresAt": plan.expires_at, "summary": preview}


def _resolve(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return resolve_state(payload, ctx.call, ctx.state_scope) if str(payload.get("expectedState", "")).startswith(PREFIX) else payload
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None
