# //(Денис 2026-10-03. Версия MCP 1.2.0
# Постоянные проверки дерева назначений, иерархии, типов и ограничения области счетов.
"""Assigned account kinds, hierarchy, primitive types and bounded trees."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from extractor1c.projects_api import AccountScope, AccountAnalyticsRequest
from extractor1c.reference_graph import analyze_reference_graph

FACT = "РегистрБухгалтерии.Хозрасчетный"
CHART = "ПланСчетов.Хозрасчетный"
PVH = "ПланВидовХарактеристик.ВидыСубконтоХозрасчетные"
CATALOG = "Справочник.БанковскиеСчетаОрганизаций"

def analytics():
    kind = {"id": "kind1", "name": "Банковские счета", "characteristicPlan": PVH,
            "valueTypes": [{"type": "Банковский счет", "object": CATALOG},
                           {"type": "Строка", "object": None}]}
    return {"chartOfAccounts": CHART, "warnings": [], "truncated": False,
            "accounts": [{"id": "a51", "parentId": "", "code": "51", "name": "Расчетные счета", "subcontoKinds": [kind]},
                         {"id": "a511", "parentId": "a51", "code": "51.1", "name": "Рублевые", "subcontoKinds": [deepcopy(kind)]}]}

def backend(data=None):
    calls = []
    def call(route, body):
        calls.append((route, body))
        if route == "metadata/describe":
            return [{"name": "Субконто1", "metadataTargets": [CATALOG, "Справочник.НеНазначенный"]}]
        assert route == "accounts/subconto/describe"
        return deepcopy(data if data is not None else analytics())
    return call, calls

def walk(node):
    yield node
    for child in node["children"]:
        yield from walk(child)

def test_account_scope_reads_assignments_and_preserves_duplicate_paths():
    api, calls = backend()
    graph = analyze_reference_graph(api, FACT, account_scope={"codes": ["51"]})
    nodes = list(walk(graph["tree"]))
    assert graph["complete"] and not graph["truncated"]
    assert graph["accountAnalytics"]["exportCandidates"] == [CATALOG]
    assert sum(n.get("object") == CATALOG for n in nodes) == 2
    assert sum(n.get("type") == "Строка" and not n["exportCandidate"] for n in nodes) == 2
    account = next(n for n in nodes if n.get("code") == "51")
    assert any(c.get("code") == "51.1" for c in account["children"])
    assert not any(n.get("object") == "Справочник.НеНазначенный" for n in nodes)
    assert calls[-1] == ("accounts/subconto/describe", {"object": FACT, "codes": ["51"], "includeSubaccounts": True, "maxAccounts": 200, "maxKinds": 1000})

def test_no_scope_retains_metadata_union_and_never_reads_account_data():
    api, calls = backend()
    graph = analyze_reference_graph(api, FACT)
    assert len(graph["edges"]) == 2
    assert all(route == "metadata/describe" for route, _ in calls)

def test_tree_limit_marks_partial_result():
    api, _ = backend()
    graph = analyze_reference_graph(api, FACT, max_nodes=5, account_scope={"codes": ["51"]})
    assert not graph["complete"] and graph["truncated"]
    assert len(list(walk(graph["tree"]))) <= 5

def test_missing_account_and_backend_limits_are_not_success():
    data = analytics()
    data.update(accounts=[], warnings=["account_not_found_or_inaccessible:51"])
    api, _ = backend(data)
    graph = analyze_reference_graph(api, FACT, account_scope={"codes": ["51"]})
    assert not graph["complete"]
    assert not graph["accountAnalytics"]["exportCandidates"]

def test_cyclic_account_hierarchy_is_rejected():
    data = analytics()
    data["accounts"][0]["parentId"] = "a511"
    api, _ = backend(data)
    with pytest.raises(ValueError, match="Cyclic"):
        analyze_reference_graph(api, FACT, account_scope={"codes": ["51"]})

@pytest.mark.parametrize("scope", [{"codes": []}, {"codes": ["51", "51"]}, {"codes": ["\n"]}, {"codes": ["51"], "maxAccounts": 0}, {"codes": ["51"], "query": "arbitrary"}])
def test_account_scope_rejects_invalid_or_arbitrary_input(scope):
    with pytest.raises(ValidationError):
        AccountScope.model_validate(scope)

def test_account_api_only_accepts_exact_register_or_chart():
    for source in (FACT, CHART):
        AccountAnalyticsRequest(object=source, codes=["51"])
    with pytest.raises(ValidationError):
        AccountAnalyticsRequest(object="Документ.ЗаказПоставщику", codes=["51"])
def test_enum_is_terminal_and_virtual_register_uses_base_metadata():
    calls = []
    def api(route, body):
        calls.append((route, body))
        assert route == "metadata/describe"
        if body["object"] == "Перечисление.ВариантыОплаты":
            return []
        return [{"name": "ВариантОплаты", "metadataTargets": ["Перечисление.ВариантыОплаты"]}]
    enum = analyze_reference_graph(api, "Перечисление.ВариантыОплаты")
    assert enum["complete"] and enum["tree"]["children"] == []
    graph = analyze_reference_graph(api, "РегистрНакопления.Выручка.Обороты")
    assert graph["tree"]["children"][0]["object"] == "Перечисление.ВариантыОплаты"
    assert calls[-1][1]["object"] == "РегистрНакопления.Выручка"

# \\\Денис 2026-10-03. Версия MCP 1.2.0)
