# //(Денис 2026-10-03. Версия MCP 1.2.0
# Протокол общего анализа с account_scope и новой версией readiness.
"""Exercise the MCP wire protocol and the preview-bound write flow."""
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from extractor1c.mcp_server import app, get_project_plan, preview_project, update_project
from extractor1c.operation_store import OperationStore
from extractor1c.projects_api import ProjectRowChange, ProjectRowsAppend

TOKEN = "secret-token"
BEARER = "Bearer " + TOKEN


GRAPH_SCENARIOS = [
    ({"object": "РегистрНакопления.ВыручкаИСебестоимостьПродаж",
      "expand_paths": ["АналитикаУчетаНоменклатуры.Номенклатура", "АналитикаУчетаПоПартнерам",
                       "АналитикаУчетаПартий", "АналитикаУчетаНаборов"]},
     "АналитикаУчетаНоменклатуры.Номенклатура.ЕдиницаИзмерения"),
    ({"object": "Документ.ЗаказКлиента"}, "Товары.Номенклатура"),
]


def check_reference_graph_scenarios_over_mcp(http):
    metadata = json.loads((Path(__file__).parent / "fixtures/reference_graph_erp.json").read_text(encoding="utf-8"))["metadata"]

    def backend(route, body):
        if route == "metadata/list":
            return sorted(metadata)
        assert route == "metadata/describe"
        return metadata[body["object"]]

    # The MCP session manager can only start once; reuse the protocol test's client.
    for arguments, expected_path in GRAPH_SCENARIOS:
        with patch("extractor1c.mcp_server.call_api", side_effect=backend):
            result = rpc(http, "tools/call", {"name": "analyze_reference_graph", "arguments": arguments})
        assert not result["isError"]
        graph = json.loads(result["content"][0]["text"])
        assert graph["complete"]
        assert any(e["path"] == expected_path for e in graph["edges"])


def check_partition_scenarios_over_mcp(http):
    captured = json.loads((Path(__file__).parent / "fixtures/project_segments_erp.json").read_text(encoding="utf-8"))["cases"]
    for case in captured:
        definition = case["definition"]

        def backend(route, body):
            if route == "connections/list":
                return [{"name": definition["connectionName"]}]
            assert route == "projects/preview"
            assert body == definition
            return case["preview"]

        with patch("extractor1c.mcp_server.call_api", side_effect=backend):
            result = rpc(http, "tools/call", {"name": "preview_project", "arguments": {"definition": definition}})
        assert not result["isError"]
        preview = json.loads(result["content"][0]["text"])
        assert preview["approved"]
        settings = preview["summary"]["rows"][0]["sourceSettings"]
        assert settings["maxItemsPerBatch"] == definition["maxItemsPerBatch"]
        assert settings["grouping"] == case["preview"]["sourceSettings"]["grouping"]
        result = rpc(http, "tools/call", {"name": "get_project_plan", "arguments": {"plan_id": preview["planId"]}})
        assert not result["isError"]
        plan = json.loads(result["content"][0]["text"])
        assert plan["summary"]["rows"][0]["sourceSettings"] == settings


@pytest.fixture(autouse=True)
def _configure_auth(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    with (
        patch("extractor1c.mcp_auth.current_tokens", return_value={TOKEN}),
        patch("extractor1c.mcp_server.operation_store", return_value=store),
    ):
        yield store


def rpc(http, method, params, request_id=1):
    response = http.post("/mcp", json={
        "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
    }, headers={"Accept": "application/json, text/event-stream", "Authorization": BEARER})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_mcp_protocol_and_backend_dispatch():
    assert isinstance(app, FastAPI)
    with TestClient(app, base_url="http://127.0.0.1:8001") as http:
        initialized = rpc(http, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        })
        assert initialized["serverInfo"]["name"] == "extractor1c"
        assert initialized["serverInfo"]["version"] == "3.2.0"
        assert "preview_project" in initialized["instructions"]
        assert "list_skills" in initialized["instructions"]
        assert "get_skill" in initialized["instructions"]
        assert 'document="SKILL.md"' in initialized["instructions"]
        assert "references/10-mcp-projects.md" in initialized["instructions"]
        check_reference_graph_scenarios_over_mcp(http)
        check_partition_scenarios_over_mcp(http)
        with patch("extractor1c.mcp_server.call_api", side_effect=lambda route, body:
                   {"methodsVersion": "1.2.0", "capabilities": ["account_subconto_settings"]}
                   if route == "service/readiness" else [{"name": "test"}]):
            ready_result = rpc(http, "tools/call", {"name": "readiness", "arguments": {}})
        assert not ready_result["isError"]
        ready = json.loads(ready_result["content"][0]["text"])
        assert ready["ready"] is True
        assert ready["connectionCount"] == 1
        assert ready["oneCMethodsVersion"] == "1.2.0"
        assert ready["message"] in initialized["instructions"]
        listed = rpc(http, "tools/list", {})
        tools = {tool["name"]: tool for tool in listed["tools"]}
        assert len(tools) == 31
        assert {"get_project_template_schema", "validate_project_template", "preview_project_template"} <= set(tools)
        schema_result = rpc(http, "tools/call", {"name": "get_project_template_schema", "arguments": {}})
        assert not schema_result["isError"]
        assert json.loads(schema_result["content"][0]["text"])["$id"] == "urn:denvic:extractor1c:project-template-request:1"
        assert "analyze_accumulation_register" not in tools
        assert tools["analyze_reference_graph"]["annotations"]["readOnlyHint"] is True
        assert set(tools["analyze_reference_graph"]["inputSchema"]["properties"]) == {
            "object", "expand_paths", "max_depth", "max_nodes", "max_edges", "include_tabular_sections", "account_scope", "base_id",
        }
        with patch("extractor1c.mcp_server.call_api", return_value=[]):
            graph_result = rpc(http, "tools/call", {
                "name": "analyze_reference_graph", "arguments": {"object": "Справочник.Номенклатура"},
            })
        assert not graph_result["isError"]
        graph = json.loads(graph_result["content"][0]["text"])
        assert graph["object"] == "Справочник.Номенклатура"
        assert graph["nodes"][0]["expanded"] is True
        assert tools["preview_project_initialization"]["annotations"]["readOnlyHint"] is True
        assert tools["initialize_project_queue"]["annotations"]["readOnlyHint"] is False
        assert set(tools["initialize_project_queue"]["inputSchema"]["properties"]) == {"plan_id", "operation_id", "base_id"}
        assert tools["get_project_export_status"]["annotations"]["readOnlyHint"] is True
        assert tools["preview_project_export"]["annotations"]["readOnlyHint"] is True
        assert tools["start_project_export"]["annotations"]["readOnlyHint"] is False
        assert set(tools["start_project_export"]["inputSchema"]["properties"]) == {"plan_id", "operation_id", "base_id"}
        assert tools["get_project_schedule"]["annotations"]["readOnlyHint"] is True
        assert tools["preview_project_schedule"]["annotations"]["readOnlyHint"] is True
        assert tools["set_project_schedule"]["annotations"]["readOnlyHint"] is False
        assert set(tools["set_project_schedule"]["inputSchema"]["properties"]) == {"plan_id", "operation_id", "base_id"}
        assert tools["list_skills"]["annotations"]["readOnlyHint"] is True
        assert tools["get_skill"]["annotations"]["readOnlyHint"] is True
        assert tools["search_metadata"]["annotations"]["readOnlyHint"] is True
        assert tools["create_project"]["annotations"]["readOnlyHint"] is False
        assert tools["create_project"]["annotations"]["idempotentHint"] is True
        assert tools["preview_project"]["annotations"]["readOnlyHint"] is True
        listed_skills = rpc(http, "tools/call", {"name": "list_skills", "arguments": {}})
        assert not listed_skills["isError"]
        skills = json.loads(listed_skills["content"][0]["text"])["skills"]
        local = next(item for item in skills if item["id"] == "extractor1c")
        assert "SKILL.md" in local["documents"]
        assert "references/10-mcp-projects.md" in local["documents"]
        forbidden = set("→←↔↓—–‑…")
        local["description"].encode("cp1251")
        assert forbidden.isdisjoint(local["description"])
        for document in local["documents"]:
            published = rpc(http, "tools/call", {"name": "get_skill", "arguments": {
                "skill_id": local["id"], "document": document,
            }})
            published_text = json.loads(published["content"][0]["text"])["content"]
            published_text.encode("cp1251")
            assert forbidden.isdisjoint(published_text)
        read = rpc(http, "tools/call", {"name": "get_skill", "arguments": {
            "skill_id": local["id"], "document": "SKILL.md",
        }})
        assert not read["isError"]
        assert "# Экстрактор 1С" in json.loads(read["content"][0]["text"])["content"]
        reference = rpc(http, "tools/call", {"name": "get_skill", "arguments": {
            "skill_id": local["id"], "document": "references/10-mcp-projects.md",
        }})
        assert not reference["isError"]
        assert json.loads(reference["content"][0]["text"])["document"] == "references/10-mcp-projects.md"
        segmentation = rpc(http, "tools/call", {"name": "get_skill", "arguments": {
            "skill_id": local["id"], "document": "references/15-accumulation-registers.md",
        }})
        segmentation_text = json.loads(segmentation["content"][0]["text"])["content"]
        assert "`0` для сегментов по периоду" in segmentation_text
        assert "`0` для сегментов по префиксу строки" in segmentation_text
        assert "`1000` только для сегмента `По значению`" in segmentation_text
        assert "Пересеки список кандидатов с фактическими именами полей" in segmentation_text
        assert "Если поле `Дата` существует и имеет тип `Дата`" in segmentation_text
        assert "`maxItemsPerBatch: 0`" in segmentation_text
        rejected = rpc(http, "tools/call", {"name": "get_skill", "arguments": {
            "skill_id": local["id"], "document": "../../.env",
        }})
        assert rejected["isError"]
        with patch("extractor1c.mcp_server.call_api", return_value={"objects": ["Catalog.Test"]}) as backend:
            result = rpc(http, "tools/call", {"name": "list_metadata", "arguments": {}})
            assert not result["isError"]
            assert json.loads(result["content"][0]["text"]) == {"objects": ["Catalog.Test"]}
            backend.assert_called_once_with("metadata/list", {})
        with patch("extractor1c.mcp_server.call_api") as backend:
            result = rpc(http, "tools/call", {"name": "create_project", "arguments": {
                "definition": {"code": "unapproved code"},
            }})
            assert result["isError"]
            backend.assert_not_called()


def test_preview_binds_payload_and_completed_operation_replays():
    row = {
        "object": "Справочник.Номенклатура", "projectName": "Project",
        "connectionName": "demo Clickhouse - erp_25_demo_denis",
        "tableName": "Articles", "segment": "",
    }
    append = ProjectRowsAppend(
        projectName="Project", connectionName=row["connectionName"],
        expectedState="state", rows=[row],
    )
    preview_value = {"projectName": "Project", "stateMatches": True, "rows": [{
        "object": row["object"], "table": "Articles", "fields": [{"target": "ArticleGuid"}],
        "tableExists": False, "projectTableExists": False,
    }]}
    with patch("extractor1c.mcp_server.call_api", side_effect=lambda route, body: (
        [{"name": row["connectionName"]}] if route == "connections/list" else
        {"connectionName": row["connectionName"]} if route == "projects/verify" else preview_value
    )) as backend:
        planned = preview_project(append)
        # Опциональная сегментация не отправляется как null старому API.
        backend.assert_called_with("projects/preview", append.model_dump(exclude_none=True))
    assert planned["approved"] is True
    operation_id = str(uuid.uuid4())
    with patch("extractor1c.mcp_server.call_api", return_value={"ok": True}) as backend:
        first = update_project(planned["planId"], operation_id)
        second = update_project(planned["planId"], operation_id)
        # Новый необязательный параметр не меняет прежний payload записи.
        backend.assert_called_once_with("projects/update", append.model_dump(exclude_none=True))
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["result"] == {"ok": True}


def test_row_update_preview_allows_its_existing_table():
    row = {
        "object": "Справочник.Партнеры", "projectName": "Project",
        "connectionName": "demo Clickhouse - erp_25_demo_denis",
        "tableName": "Partners", "segment": "",
    }
    change = ProjectRowChange(
        projectName="Project", currentTableName="Partners",
        expectedState="state", definition=row,
    )
    preview_value = {
        "projectName": "Project", "stateMatches": True, "conflict": False,
        "object": row["object"], "table": "Partners", "fields": [{"target": "PartnerGuid"}],
        "tableExists": True,
    }
    with patch("extractor1c.mcp_server.call_api", side_effect=lambda route, body: (
        [{"name": row["connectionName"]}] if route == "connections/list" else
        {"connectionName": row["connectionName"]} if route == "projects/verify" else preview_value
    )):
        planned = preview_project(change)
    assert planned["approved"] is True
    assert get_project_plan(planned["planId"])["summary"]["approved"] is True

    preview_value["stateMatches"] = False
    preview_value["conflict"] = True
    with patch("extractor1c.mcp_server.call_api", side_effect=lambda route, body: (
        [{"name": row["connectionName"]}] if route == "connections/list" else
        {"connectionName": row["connectionName"]} if route == "projects/verify" else preview_value
    )):
        rejected = preview_project(change)
    assert rejected["approved"] is False
# \\\Денис 2026-10-03. Версия MCP 1.2.0)
