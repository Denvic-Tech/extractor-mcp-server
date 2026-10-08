# //(Денис 2026-10-03. Версия MCP 1.2.0
# Дерево путей и назначений счетов с лимитами, составными типами и повторными ветвями.
"""Bounded occurrence trees: graph routes stay distinct for polymorphic links."""

def build_tree(root_name, edges, limit):
    root = {"id": "t0", "object": root_name, "relation": "fact", "children": []}
    state = {"root": root, "count": 1, "truncated": False}
    for edge in edges:
        parent = root
        for step in edge["steps"]:
            key = (step["field"], step["target"], step["relation"])
            child = next((c for c in parent["children"] if c.get("key") == key), None)
            if child is None:
                child = append_node(state, parent, limit, object=step["target"], field=step["field"], relation=step["relation"], evidence="metadata")
                if child is None:
                    break
                child["key"] = key
            parent = child
        else:
            parent.update(status=edge["status"], path=edge["path"])
            for key in ("join", "reason"):
                if key in edge:
                    parent[key] = edge[key]
            if edge["status"] in {"cycle", "identity"}:
                parent["referenceToObject"] = edge["target"]
    def clean(node):
        node.pop("key", None)
        for child in node["children"]:
            clean(child)
    clean(root)
    return state


def append_node(state, parent, limit, **attributes):
    if state["count"] >= limit:
        state["truncated"] = True
        return None
    node = {"id": "t" + str(state["count"]), **attributes, "children": []}
    state["count"] += 1
    parent["children"].append(node)
    return node


def add_account_tree(state, analytics, limit, max_depth):
    if not isinstance(analytics, dict) or not isinstance(analytics.get("accounts"), list) or not isinstance(analytics.get("chartOfAccounts"), str):
        raise ValueError("Invalid account analytics response")
    warnings, candidates = set(), set()
    chart = append_node(state, state["root"], limit, object=analytics["chartOfAccounts"], relation="account_analytics", evidence="assigned_account_settings")
    if chart is None:
        return candidates, {"max_tree_nodes"}
    accounts = analytics["accounts"]
    by_id = {a["id"]: a for a in accounts}
    if len(by_id) != len(accounts):
        raise ValueError("Duplicate account identifiers")
    created, active, account_depths = {}, set(), {}
    def account_node(account, ancestry_depth=0):
        identifier = account["id"]
        if identifier in created:
            return created[identifier]
        if identifier in active:
            raise ValueError("Cyclic account hierarchy")
        if ancestry_depth >= max_depth:
            warnings.add("max_depth:account_hierarchy")
            state["truncated"] = True
            return None
        active.add(identifier)
        parent_account = by_id.get(account.get("parentId"))
        parent = account_node(parent_account, ancestry_depth + 1) if parent_account else chart
        active.remove(identifier)
        actual_depth = account_depths.get(account.get("parentId"), 0) + 1
        if actual_depth > max_depth:
            parent = None
            state["truncated"] = True
            warnings.add("max_depth:account_hierarchy")
        account_depths[identifier] = actual_depth
        node = append_node(state, parent, limit, relation="account", accountId=identifier, code=account["code"], name=account["name"]) if parent else None
        created[identifier] = node
        return node
    for account in accounts:
        parent = account_node(account)
        if parent is None:
            continue
        for kind in account["subcontoKinds"]:
            node = append_node(state, parent, limit, relation="assigned_subconto", kindId=kind["id"], name=kind["name"], settings=kind.get("settings", {}))
            if node is None:
                break
            pvh = append_node(state, node, limit, relation="characteristic_plan", object=kind["characteristicPlan"])
            if pvh is None:
                break
            for value in kind["valueTypes"]:
                target = value.get("object")
                if target:
                    from .projects_api import DescribeObjectRequest
                    DescribeObjectRequest(object=target)
                leaf = append_node(state, pvh, limit, relation="allowed_value_type", type=value["type"], object=target, exportCandidate=bool(target), evidence="assigned_kind_value_type")
                if leaf is not None and target:
                    candidates.add(target)
    if state["truncated"]:
        warnings.add("max_tree_nodes" if state["count"] >= limit else "account_tree_truncated")
    return candidates, warnings
# \\\Денис 2026-10-03. Версия MCP 1.2.0)
