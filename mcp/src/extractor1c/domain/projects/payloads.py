"""Признаки полезной нагрузки проекта, от которых зависят требуемые возможности EPA."""
from typing import Any


def uses_query_source(payload: dict[str, Any]) -> bool:
    """Источник «Запрос» в одиночном, заменяемом или многострочном определении."""
    if "queryText" in payload:
        return True
    definition = payload.get("definition")
    if isinstance(definition, dict) and uses_query_source(definition):
        return True
    return any(uses_query_source(row) for row in payload.get("rows", []) if isinstance(row, dict))


def uses_column_names(payload: dict[str, Any]) -> bool:
    """Находит новые имена в одиночном, заменяемом и многострочном определении."""
    if payload.get("columnNames"):
        return True
    definition = payload.get("definition")
    if isinstance(definition, dict) and uses_column_names(definition):
        return True
    return any(uses_column_names(row) for row in payload.get("rows", []) if isinstance(row, dict))


def uses_dotted_field_addition(payload: dict[str, Any]) -> bool:
    """Отличить новое добавление по пути от правок уже сохранённых полей."""
    return any(
        change.get("add") is True and "." in change["source"]
        for change in payload.get("fieldChanges", [])
    )


def matches_field_mapping_row(payload: dict[str, object], response: dict[str, object]) -> bool:
    """Ответ 1С относится к той же сохранённой строке, что и запрос изменения полей."""
    if "rowIdentifier" in payload:
        return str(response.get("rowIdentifier")) == str(payload["rowIdentifier"])
    return response.get("currentTableName") == payload["currentTableName"]
