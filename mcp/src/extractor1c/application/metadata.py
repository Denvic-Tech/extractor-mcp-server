"""Чтение метаданных 1С: поиск объектов, поля объекта, граф ссылок.

База с транспортом DCC читает неизменяемый снимок метаданных; прямая база —
живые методы EPA. Автоматического перехода между маршрутами нет.
"""
import re
from typing import Any

from ..domain.paging import list_payload, page
from .errors import ApplicationError
from .ports import AppContext, CallApi
from .reference_graph import analyze_reference_graph


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
    values = list_payload(call_api("metadata/list", {}), "objects", "items")
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
    fields = list_payload(call_api("metadata/describe", {"object": object_name}), "fields", "items")
    folded = name_filter.casefold().strip()
    filtered = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        field = {
            key: raw.get(key) for key in ("name", "type", "types", "kind", "metadataTargets")
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


def search(ctx: AppContext, query: str, kinds: list[str] | None, cursor: int, limit: int,
           snapshot_id: str | None) -> Any:
    """Имена объектов метаданных постранично, без выдачи полного каталога."""
    try:
        if ctx.transport == "dcc":
            result = ctx.read_snapshot("objects", snapshot_id, params={
                "query": query, "kinds": kinds or [], "cursor": cursor, "limit": limit})
            descriptions = result["items"]
            if any(not isinstance(item, dict) or not isinstance(item.get("fullName"), str)
                   for item in descriptions):
                raise ApplicationError("Invalid DCC metadata object names")
            return {**result, "items": [item["fullName"] for item in descriptions],
                    "objectDescriptions": descriptions}
        if snapshot_id is not None:
            raise ValueError("snapshot_id requires DCC transport")
        return search_metadata(ctx.call, query, kinds, cursor, limit)
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None


def describe_fields(ctx: AppContext, object_name: str, name_filter: str, reference_only: bool,
                    cursor: int, limit: int, snapshot_id: str | None) -> Any:
    """Компактная страница полей объекта с точными целями ссылок."""
    try:
        if ctx.transport == "dcc":
            return ctx.read_snapshot("fields", snapshot_id, params={
                "object": object_name, "name_filter": name_filter, "reference_only": reference_only,
                "cursor": cursor, "limit": limit})
        if snapshot_id is not None:
            raise ValueError("snapshot_id requires DCC transport")
        return describe_object_page(ctx.call, object_name, name_filter, reference_only, cursor, limit)
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None


def reference_graph(ctx: AppContext, object_name: str, expand_paths: list[str] | None, max_depth: int,
                    max_nodes: int, max_edges: int, include_tabular_sections: bool,
                    account_scope: dict | None, snapshot_id: str | None) -> Any:
    """Исходящие ссылки объекта. Область счетов читается только живыми методами EPA."""
    try:
        if ctx.transport == "dcc" and account_scope is None:
            return ctx.read_snapshot("reference-graph", snapshot_id, body={
                "object": object_name, "expand_paths": expand_paths or [], "max_depth": max_depth,
                "max_nodes": max_nodes, "max_edges": max_edges,
                "include_tabular_sections": include_tabular_sections})
        if snapshot_id is not None:
            raise ValueError("snapshot_id requires DCC snapshot graph without live account_scope")
        return analyze_reference_graph(
            ctx.call, object_name, expand_paths, max_depth, max_nodes, max_edges,
            include_tabular_sections, account_scope,
        )
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None
