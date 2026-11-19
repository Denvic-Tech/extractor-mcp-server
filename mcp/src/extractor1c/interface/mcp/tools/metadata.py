"""Инструменты чтения метаданных 1С."""
from typing import Any

from ....application import metadata
from ....domain.decisions import field_choices, graph_choices
from ..context import CONTEXT
from ..registry import READ, base_tool, threaded_tool


@threaded_tool(annotations=READ)
@base_tool
def list_metadata() -> Any:
    """Read all 1C metadata objects. Prefer search_metadata for hosted clients."""
    return CONTEXT.call("metadata/list", {})


@threaded_tool(annotations=READ)
@base_tool
def get_metadata_snapshot(snapshot_id: str | None = None) -> dict[str, Any]:
    """Read DCC snapshot provenance; reuse snapshotId for paging and project plans."""
    return CONTEXT.read_snapshot("snapshot", snapshot_id)


@threaded_tool(annotations=READ)
@base_tool
def search_metadata(query: str = "", kinds: list[str] | None = None,
                    cursor: int = 0, limit: int = 50,
                    snapshot_id: str | None = None) -> Any:
    """Search and page metadata names without returning the complete metadata catalog."""
    return metadata.search(CONTEXT, query, kinds, cursor, limit, snapshot_id)


@threaded_tool(annotations=READ)
@base_tool
def describe_object(object: str) -> Any:
    """Read every field of a 1C metadata object. Prefer describe_object_fields."""
    return CONTEXT.call("metadata/describe", {"object": object})


@threaded_tool(annotations=READ)
@base_tool
def describe_object_fields(object: str, name_filter: str = "",
                           reference_only: bool = False, cursor: int = 0,
                           limit: int = 100, snapshot_id: str | None = None, details: bool = False) -> Any:
    """Read a compact, filtered page of object fields and exact metadata targets."""
    result = metadata.describe_fields(CONTEXT, object, name_filter, reference_only, cursor, limit, snapshot_id)
    return result if details else field_choices(result, object)


@threaded_tool(annotations=READ)
@base_tool
def analyze_reference_graph(object: str, expand_paths: list[str] | None = None,
                            max_depth: int = 4, max_nodes: int = 500,
                            max_edges: int = 2000,
                            include_tabular_sections: bool = True,
                            account_scope: dict | None = None,
                            snapshot_id: str | None = None, details: bool = False) -> Any:
    """Find outgoing references and dotted snowflake paths for any 1C entity.

    Expand selected paths and nonperiodic information registers. Skip periodic
    registers. Discover document tabular sections with the updated 1C API.
    Return exact polymorphic branches, join hints, cycles and explicit limits.
    """
    result = metadata.reference_graph(
        CONTEXT, object, expand_paths, max_depth, max_nodes, max_edges,
        include_tabular_sections, account_scope, snapshot_id,
    )
    return graph_choices(result, details=details)
