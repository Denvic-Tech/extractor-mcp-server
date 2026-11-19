from types import SimpleNamespace
from extractor1c.application.metadata import describe_object_page, search_metadata
from extractor1c.application.project_reading import verify_project_summary


def test_metadata_search_and_field_paging():
    def api(route, body):
        if route == "metadata/list":
            return ["Справочник.Валюты", "Документ.Заказ", "Справочник.Контрагенты"]
        return [
            {"name": "Ссылка", "type": "CatalogRef", "kind": "СтандартныйРеквизит",
             "metadataTargets": ["Справочник.Валюты"]},
            {"name": "Код", "type": "String", "kind": "СтандартныйРеквизит", "metadataTargets": []},
        ]
    result = search_metadata(api, "конт", ["Справочник"], 0, 10)
    assert result["items"] == ["Справочник.Контрагенты"]
    fields = describe_object_page(api, "Справочник.Валюты", reference_only=True)
    assert [item["name"] for item in fields["items"]] == ["Ссылка"]


def test_verify_summary_replaces_large_state_token():
    state = {"projectName": "P", "projectRef": "guid", "fieldsMapped": 1, "rows": [{
        "source": "Справочник.Валюты", "table": "Currencies", "fields": [
            {"source": "СсылкаГуид", "target": "CurrencyGuid", "type": "UUID"}
        ],
    }]}
    result = verify_project_summary(SimpleNamespace(call=lambda route, body: {**state, "stateToken": "x" * 100_000}, state_scope="test"), "P")
    assert result["stateToken"].startswith("mcp-state-v1:")
    assert len(result["stateToken"]) < 100
    assert result["rows"][0]["guidFields"][0]["target"] == "CurrencyGuid"
