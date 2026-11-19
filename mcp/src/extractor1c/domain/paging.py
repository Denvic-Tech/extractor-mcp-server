"""Постраничная выдача и разбор списочных ответов 1С."""
from typing import Any


def list_payload(value: Any, *keys: str) -> list[Any]:
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
