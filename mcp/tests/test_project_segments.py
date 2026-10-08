"""Permanent contract regression for catalog/document partition choices."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from extractor1c.cloud_tools import preview_summary
from extractor1c.mcp_server import get_project_plan, preview_project
from extractor1c.operation_store import OperationStore
from extractor1c.projects_api import ProjectDefinition


CAPTURED = json.loads((Path(__file__).parent / "fixtures/project_segments_erp.json").read_text(encoding="utf-8"))["cases"]
EXPECTED = {
    "document_day": ("День", "", 0, "Дата", "ДатаДень", "День"),
    "document_reference": ("По значению", "Ссылка", 1000, "Ссылка", "Ссылка", ""),
    "catalog_name_prefix": ("Первые две буквы", "Наименование", 0, "Наименование", "НаименованиеПервыеДвеБуквы", "Первые две буквы"),
    "catalog_code_prefix": ("Первые две буквы", "Код", 0, "Код", "КодПервыеДвеБуквы", "Первые две буквы"),
    "catalog_reference": ("По значению", "Ссылка", 1000, "Ссылка", "Ссылка", ""),
}


@pytest.mark.parametrize("capture", CAPTURED, ids=lambda c: c["case"])
def test_actual_1c_partition_choice_survives_preview_and_saved_plan(capture, tmp_path):
    segment, field, batch, path, parameter, function = EXPECTED[capture["case"]]
    definition = ProjectDefinition.model_validate(capture["definition"])
    assert (definition.segment, definition.segmentField, definition.maxItemsPerBatch) == (segment, field, batch)
    store = OperationStore(tmp_path / "plans.sqlite3")

    def api(route, body):
        if route == "connections/list":
            return [{"name": definition.connectionName}]
        assert route == "projects/preview", "Partition checks must never create a live project"
        # //(Денис 2026-10-03. Версия MCP 1.2.0
        # Старый запрос сохраняет прежний контракт без segmentation:null.
        assert body == definition.model_dump(exclude_none=True)
        # \\\Денис 2026-10-03. Версия MCP 1.2.0)
        return deepcopy(capture["preview"])

    with patch("extractor1c.mcp_server.call_api", side_effect=api), patch("extractor1c.mcp_server.operation_store", return_value=store):
        preview = preview_project(definition)
        assert preview["approved"] and preview["planId"]
        settings = preview["summary"]["rows"][0]["sourceSettings"]
        assert settings["maxItemsPerBatch"] == batch
        grouping = settings["grouping"]
        assert len(grouping) == 1
        assert grouping[0] == {"enabled": True, "field": parameter, "path": path,
                               "function": function, "inclusive": False}
        plan = get_project_plan(preview["planId"], limit=200)
        assert plan["summary"]["rows"][0]["sourceSettings"] == settings
        assert any(f["source"] == "Параметр." + parameter for f in plan["fields"]["items"])
        # //(Денис 2026-10-03. Версия MCP 1.2.0
        # План сохраняет точный старый запрос без необязательных null.
        assert store.get_plan(preview["planId"]).payload == definition.model_dump(exclude_none=True)
        # \\\Денис 2026-10-03. Версия MCP 1.2.0)
        assert not store.get_plan(preview["planId"]).operation_id


def test_preview_normalizes_native_null_zero_and_keeps_nonzero_batch():
    for raw, expected in ((None, 0), (0, 0), (1000, 1000), (250, 250)):
        preview = {"fields": [], "sourceSettings": {"maxItemsPerBatch": raw, "grouping": [],
                                                      "numericSettings": [{"irrelevant": "x" * 10000}]}}
        summary = preview_summary(preview)
        assert summary["rows"][0]["sourceSettings"] == {"maxItemsPerBatch": expected, "grouping": []}


@pytest.mark.parametrize("capture", CAPTURED, ids=lambda c: c["case"])
def test_minimal_field_previews_keep_segment_parameters_and_native_uuid(capture, tmp_path):
    definition = ProjectDefinition.model_validate(capture["minimalDefinition"])
    raw = capture["minimalPreview"]
    store = OperationStore(tmp_path / "minimal.sqlite3")

    def api(route, body):
        if route == "connections/list":
            return [{"name": definition.connectionName}]
        assert route == "projects/preview"
        # //(Денис 2026-10-03. Версия MCP 1.2.0
        # Проверяем совместимость прежнего явного отбора полей.
        assert body == definition.model_dump(exclude_none=True)
        # \\\Денис 2026-10-03. Версия MCP 1.2.0)
        return deepcopy(raw)

    with patch("extractor1c.mcp_server.call_api", side_effect=api), patch("extractor1c.mcp_server.operation_store", return_value=store):
        preview = preview_project(definition)
        assert preview["approved"]
        plan = get_project_plan(preview["planId"], limit=200)
        fields = plan["fields"]["items"]
        assert {f["source"] for f in fields} == set(definition.selectedFields)
        parameter = "Параметр." + EXPECTED[capture["case"]][4]
        assert parameter in definition.selectedFields
        assert any(f["source"] == "СсылкаГуид" and f["type"] == "UUID" for f in fields)
        if definition.maxItemsPerBatch == 1000:
            assert any(f["source"] == "Параметр.Ссылка" and f["type"] == "UUID" for f in fields)
        assert preview["summary"]["rows"][0]["sourceSettings"]["maxItemsPerBatch"] == definition.maxItemsPerBatch
        assert len(fields) < len(capture["preview"]["fields"])


def test_missing_native_partition_settings_are_not_invented():
    assert preview_summary({"fields": []})["rows"][0]["sourceSettings"] == {}


def test_catalog_and_document_request_contract_keeps_configurable_batches():
    # 1000 is the skill's requested default, not an object-specific MCP restriction.
    for object_name in ("Документ.ЗаказКлиента", "Справочник.Номенклатура"):
        request = ProjectDefinition(object=object_name, projectName="Custom", tableName="Custom",
                                    segment="По значению", segmentField="Ссылка", maxItemsPerBatch=250)
        assert request.maxItemsPerBatch == 250
