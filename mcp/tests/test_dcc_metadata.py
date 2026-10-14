"""Snapshot identity, fixed DCC routes and MCP transport isolation."""
from uuid import uuid4

import pytest

from extractor1c.dcc_metadata import DccMetadataClient
from extractor1c.projects_api import ProjectsApiError
from tests.test_transport import Session, settings

BASE = str(uuid4())
SNAPSHOT = str(uuid4())


def page(**overrides):
    return {"snapshotId": SNAPSHOT, "sourceVersions": {"Catalogs": 3}, "warnings": [],
            "receivedAt": None, "items": [], "total": 0, "nextCursor": None, **overrides}


def client(response):
    session = Session([{"data": {"access_token": "token"}}, {"data": response}])
    return DccMetadataClient(settings(), session=session), session


def test_snapshot_fields_preserve_compound_types_and_warnings():
    fields = [{"name": "Владелец", "types": [{"name": "CatalogRef.Партнеры"},
               {"name": "CatalogRef.Контрагенты"}], "metadataTargets": ["Справочник.Партнеры", "Справочник.Контрагенты"]}]
    payload = page(items=fields, total=1, metadataEnriched=False, objectWarnings=["unknown_type"])
    api, session = client(payload)
    assert api.read(BASE, "fields", snapshot_id=SNAPSHOT, params={"object": "Справочник.Номенклатура"}) == payload
    request = session.calls[-1]
    assert request[0] == "GET" and request[1].endswith(f"/{BASE}/metadata-browser/fields")
    assert request[2]["params"]["snapshot_id"] == SNAPSHOT
    assert all(url.startswith("https://dcc.test/api/") for _, url, _ in session.calls)


def test_mcp_fields_preserve_characteristics_from_old_platform(monkeypatch):
    """Missing modern characteristic properties must not erase older metadata."""
    from extractor1c import mcp_server as server
    from extractor1c.transport import Target

    target = Target(settings(), BASE, "identity")
    monkeypatch.setattr(server, "resolve_target", lambda *args: target)
    characteristic = {"CharacteristicType": "ПланВидовХарактеристик.Свойства"}
    payload = page(items=[{"name": "ДополнительныеРеквизиты",
                          "characteristics": [characteristic],
                          "types": [{"name": "CatalogRef.Номенклатура"}],
                          "metadataTargets": ["Справочник.Номенклатура"]}], total=1)
    calls = []

    def browser(resource, snapshot_id=None, **kwargs):
        calls.append((resource, snapshot_id, kwargs))
        return payload

    monkeypatch.setattr(server, "_read_dcc_metadata", browser)
    monkeypatch.setattr(server, "call_api", lambda *args: pytest.fail("No direct 1C fallback"))
    result = server.describe_object_fields("Справочник.Номенклатура", snapshot_id=SNAPSHOT, base_id=BASE)
    assert result == payload
    assert result["items"][0]["characteristics"] == [characteristic]
    assert calls[0][:2] == ("fields", SNAPSHOT)


def test_graph_snapshot_is_in_body_as_required_by_dcc():
    api, session = client(page(nodes=[], edges=[], tree={}, complete=False, truncated=True))
    api.read(BASE, "reference-graph", snapshot_id=SNAPSHOT, body={"object": "Документ.ЗаказКлиента"})
    request = session.calls[-1]
    assert request[0] == "POST"
    assert request[2]["json"]["snapshot_id"] == SNAPSHOT
    assert "snapshot_id" not in request[2]["params"]


def test_server_cannot_silently_switch_snapshot():
    api, _ = client(page(snapshotId=str(uuid4())))
    with pytest.raises(ProjectsApiError, match="snapshot mismatch"):
        api.read(BASE, "objects", snapshot_id=SNAPSHOT)


@pytest.mark.parametrize("overrides", [{"nextCursor": 0}, {"nextCursor": True},
                                       {"items": {}}, {"total": True}, {"sourceVersions": []}])
def test_malformed_page_is_rejected(overrides):
    api, _ = client(page(**overrides))
    with pytest.raises(ProjectsApiError):
        api.read(BASE, "objects")


def test_mcp_search_uses_browser_and_returns_compatible_names(monkeypatch):
    from extractor1c import mcp_server as server
    from extractor1c.transport import Target

    target = Target(settings(), BASE, "identity")
    monkeypatch.setattr(server, "resolve_target", lambda *args: target)
    calls = []
    def browser(resource, snapshot_id=None, **kwargs):
        calls.append((resource, snapshot_id, kwargs))
        return page(items=[{"fullName": "Справочник.Номенклатура", "kind": "Справочник"}], total=1)
    monkeypatch.setattr(server, "_read_dcc_metadata", browser)
    monkeypatch.setattr(server, "call_api", lambda *args: pytest.fail("No live task for snapshot search"))
    result = server.search_metadata(query="Номенклатура", snapshot_id=SNAPSHOT, base_id=BASE)
    assert result["items"] == ["Справочник.Номенклатура"]
    assert result["snapshotId"] == SNAPSHOT
    assert calls[0][:2] == ("objects", SNAPSHOT)


def test_direct_route_rejects_dcc_snapshot_id(monkeypatch):
    from extractor1c import mcp_server as server
    from extractor1c.projects_api import ApiSettings
    from extractor1c.transport import Target
    from mcp.server.fastmcp.exceptions import ToolError

    monkeypatch.setattr(server, "resolve_target", lambda *args: Target(ApiSettings(_env_file=None), "direct", "identity"))
    with pytest.raises(ToolError, match="requires DCC"):
        server.search_metadata(snapshot_id=SNAPSHOT, base_id="direct")


def test_preview_is_a_dcc_task_with_no_direct_1c_request(tmp_path, monkeypatch):
    from extractor1c import mcp_server as server, transport
    from extractor1c.operation_store import OperationStore
    from extractor1c.projects_api import ProjectDefinition
    from extractor1c.transport import Target
    from tests.test_transport import Response

    config = settings(operation_db=str(tmp_path / "operations.sqlite"))
    target = Target(config, BASE, "identity")
    monkeypatch.setattr(server, "resolve_target", lambda *args: target)
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    store = OperationStore(tmp_path / "plan.sqlite")
    monkeypatch.setattr(server, "operation_store", lambda: store)
    operations, calls = {}, []

    class RelaySession:
        def close(self):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            assert url.startswith("https://dcc.test/api/")
            if url.endswith("/users/login"):
                return Response({"data": {"access_token": "token"}})
            if url.endswith("/metadata-browser/snapshot"):
                return Response({"data": page(complete=True)})
            if method == "POST":
                body = kwargs["json"]
                identity, route = body["operation_id"], body["method"]
                result = ([{"name": "sink", "type": "PostgreSQL"}] if route == "connections/list"
                          else {"projectName": "Test", "fields": []})
                operations[identity] = {"operation_id": identity, "task_id": identity, "method": route,
                    "status": "completed", "result_ready": True, "http_status": 200, "result": result}
                return Response({"data": {"operation_id": identity, "task_id": identity, "status": "queued"}})
            return Response({"data": operations[url.rsplit("/", 1)[1]]})

    monkeypatch.setattr(transport.requests, "Session", RelaySession)
    monkeypatch.setattr(transport, "ProjectsApiClient", lambda *args, **kwargs: pytest.fail("No direct 1C route"))
    definition = ProjectDefinition(object="Справочник.Валюты", projectName="Test", tableName="currencies", segmentation=[])
    result = server.preview_project(definition, metadata_snapshot_id=SNAPSHOT, base_id=BASE)
    assert result["approved"] is True
    plan = store.get_plan(result["planId"])
    assert plan.preview["metadataEvidence"]["snapshotId"] == SNAPSHOT
    assert "metadataEvidence" not in plan.payload
    assert "snapshot_id" not in plan.payload
    assert [item["method"] for item in operations.values()] == ["connections/list", "projects/preview"]
    assert server.get_project_plan(result["planId"], base_id=BASE)["metadataEvidence"]["snapshotId"] == SNAPSHOT
