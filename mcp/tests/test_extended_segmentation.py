# //(Денис 2026-10-03. Версия MCP 1.2.0
# Проверяем независимые роли трёх сегментов и отсутствие ложного подтверждения старого API.
from pathlib import Path
from extractor1c.cloud_tools import verify_project_summary
from extractor1c.projects_api import ProjectDefinition
import pytest
from pydantic import ValidationError
from unittest.mock import patch
from extractor1c.mcp_server import preview_project, create_project
from extractor1c.operation_store import OperationStore


def test_three_mapping_segment_roles_are_independent():
    fields = [
        {"source": "Параметр.Регистратор", "segmentField": True, "extendedSegmentField": False, "initializationSegmentField": False},
        {"source": "Параметр.ПериодДень", "segmentField": False, "extendedSegmentField": True, "initializationSegmentField": False},
        {"source": "Параметр.ПериодМесяц", "segmentField": False, "extendedSegmentField": False, "initializationSegmentField": True},
    ]
    state = {"rows": [{"fields": fields}]}
    result = verify_project_summary(lambda *_: state, "P")["rows"][0]["segmentation"]
    assert result == {"extendedMode": None, "rolesAvailable": True, "regular": ["Параметр.Регистратор"], "extended": ["Параметр.ПериодДень"], "initialization": ["Параметр.ПериодМесяц"]}


def test_old_api_does_not_confirm_missing_segment_roles():
    state = {"rows": [{"fields": [{"source": "Параметр.Регистратор", "segmentField": True}]}]}
    result = verify_project_summary(lambda *_: state, "P")["rows"][0]["segmentation"]
    assert result["rolesAvailable"] is False
    assert result["regular"] == ["Параметр.Регистратор"]


def test_manual_bank_mapping_does_not_override_saved_source_mode():
    # Живая строка 5: галки маппинга отличаются от сохранённых группировок источника.
    state = {"rows": [{"sourceSettings": {"extendedMode": False}, "fields": [
        {"source": "Параметр.Ссылка", "segmentField": True, "extendedSegmentField": False, "initializationSegmentField": False},
        {"source": "Параметр.НаименованиеПервыеДвеБуквы", "segmentField": False, "extendedSegmentField": False, "initializationSegmentField": True},
    ]}]}
    result = verify_project_summary(lambda *_: state, "P")["rows"][0]["segmentation"]
    assert result["extendedMode"] is False
    assert result["regular"] == ["Параметр.Ссылка"]
    assert result["extended"] == []
    assert result["initialization"] == ["Параметр.НаименованиеПервыеДвеБуквы"]


def definition(**kwargs):
    return ProjectDefinition(object="РегистрБухгалтерии.Хозрасчетный", projectName="P", tableName="ledger", **kwargs)


def test_legacy_segmentation_stays_regular_and_omits_new_option():
    row = definition(segment="День")
    assert row.segmentation is None
    assert "segmentation" not in row.model_dump(exclude_none=True)


def group(**changes):
    return {"field": "Период", "segment": "День", "regular": True,
            "extended": False, "initialization": False, **changes}


def test_array_supports_independent_roles_and_omits_simple_fields():
    row = definition(segmentation=[group(), group(segment="Месяц", regular=False, initialization=True)])
    assert row.segmentation[1].initialization is True
    payload = row.model_dump(exclude_none=True)
    assert "segment" not in payload and "segmentField" not in payload
    assert payload["segmentation"] == [group(), group(segment="Месяц", regular=False, initialization=True)]
    assert definition(segmentation=[]).model_dump()["segmentation"] == []
    with pytest.raises(ValidationError):
        definition(segmentation={"regular": {"segment": "День"}})
    with pytest.raises(ValidationError):
        definition(segment="День", segmentation=[group()])


@pytest.mark.parametrize("changes", [{"segment": "SQL"}, {"field": ""}, {"field": "Период;Удалить"},
                                    {"code": "arbitrary"}, {"regular": 1},
                                    {"regular": False}])
def test_array_items_are_closed_and_validate_roles_and_paths(changes):
    with pytest.raises(ValidationError):
        definition(segmentation=[group(**changes)])


@pytest.mark.parametrize("missing", ["field", "segment", "regular", "extended", "initialization"])
def test_every_array_item_field_is_required(missing):
    item = group()
    del item[missing]
    with pytest.raises(ValidationError):
        definition(segmentation=[item])


def test_multiple_roles_on_one_group_and_native_periodicity():
    row = definition(segmentation=[group(extended=True, initialization=True)], periodicity="Регистратор")
    assert row.segmentation[0].extended
    assert row.model_dump()["periodicity"] == "Регистратор"
    assert "periodicity" not in definition(segment="День").model_dump()
    with pytest.raises(ValidationError):
        definition(segment="День", periodicity="SQL")


def test_summary_reports_actual_saved_mode_and_unknown_old_mode():
    state = {"rows": [{"fields": [], "sourceSettings": {"extendedMode": True}}]}
    assert verify_project_summary(lambda *_: state, "P")["rows"][0]["segmentation"]["extendedMode"] is True
    state["rows"][0]["sourceSettings"] = {}
    assert verify_project_summary(lambda *_: state, "P")["rows"][0]["segmentation"]["extendedMode"] is None


def test_extended_preview_preserves_exact_roles_in_plan_and_write(tmp_path):
    import uuid
    row = definition(connectionName="demo", segmentation=[group(field="Регистратор", segment="По значению"), group(regular=False, extended=True), group(segment="Месяц", regular=False, initialization=True)])
    store = OperationStore(tmp_path / "plans.sqlite3")
    expected = row.model_dump(exclude_none=True)
    def backend(route, payload):
        if route == "connections/list":
            return [{"name": "demo"}]
        assert route == "projects/preview"
        assert payload == expected
        return {"object": row.object, "table": row.tableName, "fields": [], "sourceSettings": {"extendedMode": True, "grouping": []}}
    with patch("extractor1c.mcp_server.call_api", side_effect=backend), patch("extractor1c.mcp_server.operation_store", return_value=store):
        preview = preview_project(row)
        assert preview["approved"]
        assert preview["summary"]["rows"][0]["sourceSettings"]["extendedMode"] is True
        assert store.get_plan(preview["planId"]).payload == expected
    with patch("extractor1c.mcp_server.call_api", return_value={"ok": True}) as backend, patch("extractor1c.mcp_server.operation_store", return_value=store):
        create_project(preview["planId"], str(uuid.uuid4()))
        backend.assert_called_once_with("projects/create", expected)
# \\\Денис 2026-10-03. Версия MCP 1.2.0)
