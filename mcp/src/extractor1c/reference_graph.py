# //(Денис 2026-10-03. Версия MCP 1.2.0
# Общее дерево факта, нормализация виртуальных таблиц и назначенные виды субконто по account_scope.
"""Bounded outgoing reference traversal using exact 1C metadata targets."""
from collections import deque
import re
from typing import Any

from .cloud_tools import CallApi, _list_payload
from .projects_api import DescribeObjectRequest, AccountScope, AccountAnalyticsRequest


def analyze_reference_graph(call_api: CallApi, object_name: str,
                            expand_paths: list[str] | None = None,
                            max_depth: int = 4, max_nodes: int = 500,
                            max_edges: int = 2000,
                            include_tabular_sections: bool = True,
                            account_scope: dict | None = None) -> dict[str, Any]:
    """Expand requested paths and nonperiodic registers; never infer joins by name."""
    DescribeObjectRequest(object=object_name)
    scope = AccountScope.model_validate(account_scope) if account_scope is not None else None
    if not 1 <= max_depth <= 10 or not 1 <= max_nodes <= 1000 or not 1 <= max_edges <= 10000:
        raise ValueError("Invalid graph limits: depth 1..10, nodes 1..1000, edges 1..10000")
    paths = sorted(set(expand_paths or []))
    if len(paths) > 100 or any(
        not isinstance(path, str) or len(path) > 512
        or not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*)*", path)
        for path in paths
    ):
        raise ValueError("expand_paths must contain at most 100 valid dotted field paths")

    fields_cache: dict[str, list[dict]] = {}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    warnings: set[str] = set()
    seen_paths: set[str] = set()
    names: list[str] | None = None
    truncated = False

    def fields(name: str) -> list[dict]:
        if name not in fields_cache:
            metadata_name = name
            if name.count(".") == 2 and name.startswith(("РегистрНакопления.", "РегистрБухгалтерии.")):
                if name.rsplit(".", 1)[1] not in {"Обороты", "Остатки", "ОстаткиИОбороты", "ДвиженияССубконто", "Субконто"}:
                    raise ValueError("Unsupported virtual table for reference analysis")
                metadata_name = name.rsplit(".", 1)[0]
            raw = call_api("metadata/describe", {"object": metadata_name})
            if not isinstance(raw, list) and not (
                isinstance(raw, dict) and any(isinstance(raw.get(k), list) for k in ("fields", "items"))
            ):
                raise ValueError("Invalid metadata description response")
            values = _list_payload(raw, "fields", "items")
            if any(not isinstance(f, dict) or not isinstance(f.get("name"), str) for f in values):
                raise ValueError("Invalid metadata field response")
            fields_cache[name] = values
            if any(not isinstance(f.get("metadataTargets"), list) for f in values):
                warnings.add("missing_metadata_targets:" + name)
        return fields_cache[name]

    def node(name: str) -> dict | None:
        nonlocal truncated
        if name not in nodes:
            if len(nodes) >= max_nodes:
                truncated = True
                warnings.add("max_nodes")
                return None
            nodes[name] = {"object": name, "kind": name.partition(".")[0],
                           "expanded": False, "exportCandidate": True}
        return nodes[name]

    def requested(path: str) -> bool:
        return any(p == path or p.startswith(path + ".") for p in paths)

    node(object_name)
    pending = deque([(object_name, "", (), (object_name,), 0)])
    while pending:
        source, prefix, steps, ancestors, depth = pending.popleft()
        nodes[source]["expanded"] = True
        outgoing = []
        for f in fields(source):
            if scope is not None and source == object_name and source.startswith("РегистрБухгалтерии.") and re.fullmatch(r"(?:Вид)?Субконто(?:Дт|Кт)?\d*", f["name"]):
                # Assigned account kinds replace the unconstrained metadata union.
                continue
            targets = f.get("metadataTargets", [])
            if not isinstance(targets, list):
                continue
            for target in sorted(set(t for t in targets if isinstance(t, str))):
                try:
                    DescribeObjectRequest(object=target)
                except ValueError:
                    warnings.add("invalid_metadata_target:" + source + "." + f["name"])
                    continue
                outgoing.append((f, target, "reference"))

        if include_tabular_sections and source.startswith("Документ.") and source.count(".") == 1:
            if names is None:
                raw_names = call_api("metadata/list", {})
                names = _list_payload(raw_names, "objects", "items")
                if not names or not all(isinstance(n, str) for n in names):
                    raise ValueError("Invalid metadata catalog response")
            sections = sorted(n for n in names if n.startswith(source + ".") and n.count(".") == 2)
            if not sections:
                warnings.add("tabular_sections_unconfirmed:" + source)
            outgoing.extend(({"name": n.rsplit(".", 1)[1], "kind": "ТабличнаяЧасть"},
                             n, "tabular_section") for n in sections)

        for f, target, relation in outgoing:
            if len(edges) >= max_edges:
                warnings.add("max_edges")
                truncated = True
                pending.clear()
                break
            target_node = node(target)
            if target_node is None:
                continue
            path = prefix + "." + f["name"] if prefix else f["name"]
            seen_paths.add(path)
            step = {"source": source, "field": f["name"], "target": target, "relation": relation}
            route = (*steps, step)
            edge = {**step, "path": path, "depth": depth + 1, "steps": list(route),
                    "fieldKind": f.get("kind"), "status": "leaf"}
            edges.append(edge)
            if relation == "reference":
                edge["join"] = {"sourceField": f["name"], "targetField": "Ссылка",
                                "requiresTypeDiscriminator": len(f.get("metadataTargets", [])) > 1}
            else:
                edge["join"] = {"sourceField": "Ссылка", "targetField": "Ссылка",
                                "cardinality": "one_to_many"}
            if target in ancestors:
                edge["status"] = "identity" if target == source and f["name"] == "Ссылка" else "cycle"
                continue
            register = target.startswith("РегистрСведений.")
            if register and any(v["name"] == "Период" for v in fields(target)):
                target_node.update(periodic=True, exportCandidate=False)
                edge.update(status="skipped", reason="periodic_register")
                edge.pop("join", None)
                continue
            if register:
                # A register has no universal reference key. Its fields are discoverable,
                # but an equality join needs separately verified keys/cardinality.
                target_node["periodic"] = False
                edge["join"] = {"sourceField": f["name"], "targetField": None,
                                "requiresValidation": True}
            expand = register or relation == "tabular_section" or requested(path)
            if target.startswith("Перечисление."):
                continue
            if expand:
                if depth + 1 >= max_depth:
                    edge.update(status="skipped", reason="max_depth")
                    warnings.add("max_depth:" + path)
                    truncated = True
                else:
                    edge["status"] = "expanded"
                    pending.append((target, path, route, (*ancestors, target), depth + 1))

    unresolved = [p for p in paths if p not in seen_paths]
    warnings.update("unresolved_expand_path:" + p for p in unresolved)
    result = {"object": object_name, "nodes": list(nodes.values()), "edges": edges,
            "metadataEnriched": not any(w.startswith("missing_metadata_targets:") for w in warnings),
            "truncated": truncated, "complete": not warnings, "warnings": sorted(warnings),
            "unresolvedExpandPaths": unresolved}
    from .reference_tree import build_tree, add_account_tree
    tree = build_tree(object_name, edges, max_nodes)
    result["tree"] = tree["root"]
    if tree["truncated"]:
        warnings.add("max_tree_nodes")
        result["truncated"] = True
    if scope is not None:
        # A chart can be the root or an exact target discovered by the graph.
        source = object_name.rsplit(".", 1)[0] if object_name.startswith("РегистрБухгалтерии.") and object_name.count(".") == 2 else object_name
        if not source.startswith(("РегистрБухгалтерии.", "ПланСчетов.")):
            charts = [n for n in nodes if n.startswith("ПланСчетов.")]
            if len(charts) != 1:
                raise ValueError("Account scope requires an accounting register, chart or one unambiguous linked chart")
            source = charts[0]
        request = AccountAnalyticsRequest(object=source, **scope.model_dump())
        analytics = call_api("accounts/subconto/describe", request.model_dump())
        candidates, account_warnings = add_account_tree(tree, analytics, max_nodes, max_depth)
        result["accountAnalytics"] = {"chartOfAccounts": analytics["chartOfAccounts"],
                                      "exportCandidates": sorted(candidates),
                                      "visibility": "current_user", "evidence": "assigned_account_settings"}
        warnings.update(account_warnings)
        result["truncated"] |= tree["truncated"] or analytics.get("truncated", False)
        warnings.update(analytics.get("warnings", []))
    result.update(warnings=sorted(warnings), complete=not warnings)
    return result
# \\\Денис 2026-10-03. Версия MCP 1.2.0)
