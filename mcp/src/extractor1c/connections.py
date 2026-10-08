"""Selection from live 1C connections; no process-global user preference."""
from typing import Any


def select_connection(value: Any, requested: str = "") -> dict:
    items = value.get("items") if isinstance(value, dict) else value
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str)
        or not item["name"].strip() for item in items
    ):
        raise ValueError("Invalid connections/list response")
    choices = [{"name": item["name"], "type": item.get("type", "")} for item in items]
    names = [item["name"] for item in choices]
    code, name, message = "selected", requested, "Используйте выбранное подключение для следующих проектов в этом диалоге."
    if not names:
        code, name, message = "configuration_required", "", "Настройте подключение в Экстракторе 1С, затем повторите чтение подключений."
    elif requested and requested not in names:
        code, name, message = "selection_required", "", "Выбранное подключение больше недоступно. Спросите пользователя, какое подключение использовать."
    elif (requested and names.count(requested) > 1) or (not requested and len(names) != len(set(names))):
        code, name, message = "ambiguous", "", "В 1С есть подключения с одинаковыми именами. Попросите задать уникальные имена в 1С."
    elif not requested and len(names) == 1:
        name, message = names[0], "Единственное подключение выбрано автоматически."
    elif not requested:
        code, name, message = "selection_required", "", "Спросите пользователя, какое подключение использовать для дальнейшей разработки проектов."
    return {"status": code, "connectionName": name, "choices": choices, "message": message}


def resolve_project_connection(payload: dict, call_api) -> tuple[dict, dict]:
    """Resolve once before preview, binding the exact name into every plan row."""
    target = payload.get("definition", payload)
    rows = target.get("rows", [target])
    requested = target.get("connectionName", "") or next(
        (row["connectionName"] for row in rows if row.get("connectionName")), ""
    )
    current = None
    if "expectedState" in payload:
        current = call_api("projects/verify", {"projectName": payload["projectName"]}).get("connectionName")
        if not current:
            raise ValueError("Update the 1C API extension: project verification must return connectionName")
        if requested and requested != current:
            raise ValueError("Existing project connection cannot be changed by a row update")
        requested = current
    selection = select_connection(call_api("connections/list", {}), requested)
    if selection["status"] == "selected":
        target["connectionName"] = selection["connectionName"]
        for row in rows:
            row["connectionName"] = selection["connectionName"]
    return payload, selection
