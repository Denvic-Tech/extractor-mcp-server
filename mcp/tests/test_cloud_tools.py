from extractor1c.cloud_tools import (
    describe_object_page, search_metadata,
    verify_project_summary,
)


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


def test_verify_summary_drops_large_state_token():
    state = {"projectName": "P", "projectRef": "guid", "fieldsMapped": 1, "rows": [{
        "source": "Справочник.Валюты", "table": "Currencies", "fields": [
            {"source": "СсылкаГуид", "target": "CurrencyGuid", "type": "UUID"}
        ],
    }]}
    result = verify_project_summary(lambda route, body: {**state, "stateToken": "x" * 100_000}, "P")
    assert "stateToken" not in result
    assert result["rows"][0]["guidFields"][0]["target"] == "CurrencyGuid"
