"""Closed versioned template inputs and reuse of the existing preview/write contract."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from extractor1c.project_template import ProjectTemplateRequest, project_template_schema
from extractor1c.mcp_server import validate_project_template, _preview_project_definition
from extractor1c.operation_store import OperationStore


def request():
    return {"schemaVersion": 1, "definition": {
        "object": "Документ.ЗаказКлиента", "projectName": "test_order",
        "tableName": "test_order", "connectionName": "receiver",
        "segmentation": [{"field": "Ссылка", "segment": "По значению",
                          "regular": True, "extended": False, "initialization": False}],
        "maxItemsPerBatch": 1000,
    }}


@pytest.mark.parametrize("change", [
    lambda x: x.update(schemaVersion=True),
    lambda x: x.update(schemaVersion="1"),
    lambda x: x.update(schemaVersion=2),
    lambda x: x.update(sql="SELECT 1"),
    lambda x: x["definition"].update(object="Документ.ЗаказКлиента.Товары.ЕщеЧасть"),
    lambda x: x["definition"].update(object="РегистрСведений.КурсыВалют;Удалить"),
    lambda x: x["definition"].update(maxItemsPerBatch=1),
    lambda x: x["definition"].update(selectedFields=["Ссылка", "Ссылка"]),
    lambda x: x["definition"].update(handler="Выполнить(...)"),
    lambda x: x["definition"]["segmentation"][0].update(regular=False),
    lambda x: x["definition"]["segmentation"][0].update(regular="true"),
    lambda x: x["definition"]["segmentation"].append(x["definition"]["segmentation"][0].copy()),
])
def test_reject_unsafe_or_inconsistent_input(change):
    value = request()
    change(value)
    with pytest.raises(ValidationError):
        ProjectTemplateRequest.model_validate(value)


def test_schema_matches_published_file():
    actual = project_template_schema()
    published = json.loads((Path(__file__).parents[1] / "schemas/project-template-request-v1.schema.json").read_text(encoding="utf-8"))
    assert actual == published
    assert actual["additionalProperties"] is False
    assert actual["$defs"]["TemplateDefinition"]["additionalProperties"] is False
    assert len(actual["$defs"]["Segmentation"]["anyOf"]) == 3


@pytest.mark.parametrize("source,grouping,periodicity", [
    ("РегистрСведений.КурсыВалют", "Период", None),
    ("РегистрСведений.КурсыВалют.СрезПоследних", "Период", None),
    ("РегистрНакопления.ТоварыНаСкладах", "Период", None),
    ("РегистрНакопления.ТоварыНаСкладах.Обороты", "Период", "День"),
    ("ПланВидовХарактеристик.ВидыСубконтоХозрасчетные", "Ссылка", None),
    ("Справочник.Номенклатура.ДрагоценныеМатериалы", "Ссылка", None),
    ("Документ.ЗаказКлиента.Товары", "Ссылка", None),
    ("Перечисление.ABCКлассификация", "Порядок", None),
    ("ПланСчетов.Хозрасчетный", "Ссылка", None),
    ("РегистрБухгалтерии.Хозрасчетный", "Период", None),
])
def test_native_source_profiles_preserve_source_and_periodicity(source, grouping, periodicity):
    value = request()
    value["definition"].update(object=source, periodicity=periodicity)
    value["definition"]["segmentation"][0].update(
        field=grouping, segment="День" if grouping == "Период" else "По значению")
    native = ProjectTemplateRequest.model_validate(value).native_definition().model_dump(exclude_none=True)
    assert native["object"] == source
    if periodicity is not None:
        assert native["periodicity"] == periodicity
    else:
        assert "periodicity" not in native


def test_empty_grouping_array_is_preserved_for_unpartitioned_source():
    value = request()
    value["definition"].update(object="Перечисление.ABCКлассификация", segmentation=[])
    assert ProjectTemplateRequest.model_validate(value).native_definition().segmentation == []


def test_validation_is_local_and_not_a_ready_plan():
    with patch("extractor1c.mcp_server.call_api", side_effect=AssertionError("No upstream call")):
        result = validate_project_template(ProjectTemplateRequest.model_validate(request()))
    assert result["valid"]
    assert result["metadataChecked"] is False
    assert result["planId"] is None
    assert "schemaVersion" not in result["definition"]
    assert "segment" not in result["definition"]


def test_template_reuses_native_preview_and_create_plan(tmp_path):
    definition = ProjectTemplateRequest.model_validate(request()).native_definition()
    store = OperationStore(tmp_path / "ops.sqlite")
    seen = []
    def backend(route, payload):
        seen.append((route, payload))
        if route == "connections/list":
            return [{"name": "receiver"}]
        assert route == "projects/preview"
        return {"projectName": "test_order", "source": "Документ.ЗаказКлиента",
                "table": "test_order", "fields": [{"source": "Ссылка", "target": "id", "type": "String"}],
                "errors": [], "warnings": []}
    with patch("extractor1c.mcp_server.call_api", side_effect=backend), \
         patch("extractor1c.mcp_server.operation_store", return_value=store):
        result = _preview_project_definition(definition)
    assert result["approved"]
    assert result["action"] == "create"
    assert result["planId"]
    assert [r for r, _ in seen] == ["connections/list", "projects/preview"]
    assert seen[-1][1] == definition.model_dump(exclude_none=True)
