"""Closed receiver observation requests, fixed transports and read-only isolation."""
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from extractor1c import mcp_server as server
from extractor1c import secure_main
from extractor1c.projects_api import ApiSettings, ProjectReceiverInspect, ProjectsApiClient, ProjectsApiError
from extractor1c.transport import DccClient

PROJECT = "b81d6354-710c-4199-8a4c-f11a6ab63d1b"
NATIVE = {
    "projectRef": PROJECT, "rowIdentifier": "row", "source": "Справочник.Номенклатура",
    "table": "catalog", "receiver": {"kind": "MySQL"}, "observedAt": "2026-10-14T00:00:00Z",
    "checks": {"count": {"status": "ok", "result": {"count": "9223372036854775807", "exact": True}},
               "sample": {"status": "ok", "result": {
                   "fields": [{"name": "amount", "source": "Сумма", "declaredSqlType": "decimal(24,4)",
                               "typeTag": "decimal", "encoding": "text"}],
                   "rows": [{"amount": "12345678901234567890.1234"}],
                   "returnedRows": 1, "limit": 1, "truncated": False,
                   "truncatedCells": [], "order": "unspecified"}}},
    "sourceEqualityVerified": False, "warnings": [],
}


@pytest.mark.parametrize("overrides", [
    {"projectGuid": "not-a-uuid"}, {"projectGuid": None}, {"rowIdentifier": 1},
    {"rowIdentifier": "\n"}, {"checks": []}, {"checks": ["schema", "schema"]},
    {"checks": ["sql"]}, {"checks": "schema"}, {"sampleLimit": True},
    {"sampleLimit": "1"}, {"sampleLimit": -1}, {"sampleLimit": 101},
    {"checks": ["sample"]}, {"checks": ["sample"], "sampleFields": ["code"]},
    {"sampleFields": ["code"]}, {"sampleLimit": 1},
    {"checks": ["sample"], "sampleFields": [""], "sampleLimit": 1},
    {"checks": ["sample"], "sampleFields": ["code", "code"], "sampleLimit": 1},
    {"checks": ["sample"], "sampleFields": ["co\nde"], "sampleLimit": 1},
    {"checks": ["sample"], "sampleFields": [f"f{i}" for i in range(65)], "sampleLimit": 1},
    {"sql": "SELECT 1"}, {"connectionName": "override"}, {"tableName": "override"},
    {"receiverInspection": True}, {"route": "projects/export/start"},
])
def test_malformed_receiver_request_never_reaches_http(overrides):
    session = Mock()
    api = ProjectsApiClient(ApiSettings(_env_file=None, url="https://one.test/hs/extractor-projects/v1", user="u"), session)
    with pytest.raises(ValidationError):
        api.call("projects/receiver/inspect", {"projectGuid": PROJECT, **overrides})
    session.post.assert_not_called()


def test_receiver_defaults_and_explicit_bounded_sample():
    assert ProjectReceiverInspect(projectGuid=PROJECT).model_dump() == {
        "projectGuid": PROJECT, "rowIdentifier": "", "checks": ["schema"],
        "sampleFields": [], "sampleLimit": 0,
    }
    request = ProjectReceiverInspect(projectGuid=PROJECT, checks=["schema", "count", "sample"],
                                     sampleFields=[f"f{i}" for i in range(64)], sampleLimit=100)
    assert request.sampleLimit == 100 and len(request.sampleFields) == 64


def test_fixed_direct_adapter_leaves_ordinary_verify_unchanged():
    session = Mock()
    session.post.return_value = Mock(status_code=200, headers={})
    session.post.return_value.json.return_value = NATIVE
    api = ProjectsApiClient(ApiSettings(_env_file=None, url="https://one.test/hs/extractor-projects/v1", user="u"), session)
    assert api.call("projects/receiver/inspect", {"projectGuid": PROJECT}) == NATIVE
    assert session.post.call_args.args == ("https://one.test/hs/extractor-projects/v1/projects/verify",)
    assert session.post.call_args.kwargs["json"] == {
        **ProjectReceiverInspect(projectGuid=PROJECT).model_dump(), "receiverInspection": True,
    }
    api.call("projects/verify", {"projectName": "ordinary"})
    assert session.post.call_args.kwargs["json"] == {"projectName": "ordinary"}
    session.reset_mock()
    with pytest.raises(ValueError, match="Unknown API operation"):
        api.call("projects/receiver/inspect/../../export/start", {"projectGuid": PROJECT})
    session.post.assert_not_called()


def test_dcc_uses_logical_receiver_route_without_http_marker():
    operation = str(uuid4())
    api = DccClient(ApiSettings(_env_file=None, transport="dcc", dcc_url="https://dcc.test",
                               dcc_user="u", dcc_password="p"))
    api.token = "test-token"
    api._request = Mock(side_effect=[
        {"data": {"operation_id": operation, "task_id": operation, "status": "queued"}},
        {"data": {"operation_id": operation, "task_id": operation, "status": "completed",
                  "result_ready": True, "http_status": 200, "result": NATIVE}},
    ])
    try:
        assert api.call("selected-base", "projects/receiver/inspect", {"projectGuid": PROJECT}, operation) == NATIVE
        submitted = api._request.call_args_list[0].kwargs["json"]
        assert submitted["method"] == "projects/receiver/inspect"
        assert submitted["payload"] == ProjectReceiverInspect(projectGuid=PROJECT).model_dump()
        assert "receiverInspection" not in submitted["payload"]
        api._request.reset_mock()
        with pytest.raises(ValueError, match="Unknown API operation"):
            api.call("selected-base", "projects/receiver/sql", {}, operation)
        api._request.assert_not_called()
    finally:
        api.session.close()


@pytest.mark.parametrize("version,capabilities", [
    ("3.2.2", ["receiver_inspection"]), ("4.0.0", ["receiver_inspection"]),
    (None, ["receiver_inspection"]), ("3.3", ["receiver_inspection"]),
    ("3.3.0", []), ("3.3.0", "receiver_inspection"),
])
def test_older_or_unadvertised_methods_refuse_without_receiver_call(monkeypatch, version, capabilities):
    backend = Mock(return_value={"methodsVersion": version, "capabilities": capabilities})
    monkeypatch.setattr(server, "call_api", backend)
    with pytest.raises(ToolError, match="[Rr]eceiver|receiver"):
        server.inspect_project_receiver(PROJECT)
    assert backend.call_args_list == [(("service/readiness", {}),)]


@pytest.mark.parametrize("status", [401, 404, 405, 500])
def test_readiness_failure_is_not_success_or_transport_fallback(monkeypatch, status):
    backend = Mock(side_effect=ToolError(f"1C API HTTP {status}; check publication"))
    monkeypatch.setattr(server, "call_api", backend)
    with pytest.raises(ToolError, match="3.3.0" if status in {404, 405} else str(status)):
        server.inspect_project_receiver(PROJECT)
    assert backend.call_count == 1


@pytest.mark.parametrize("version", ["3.3.0", "3.4.1"])
def test_selected_base_read_only_pass_through_has_no_project_writes(tmp_path, monkeypatch, version):
    config = ApiSettings(_env_file=None, operation_db=str(tmp_path / "unused.sqlite"), bases=[
        {"base_id": "a", "url": "https://a.test", "user": "u", "password": "p"},
        {"base_id": "b", "url": "https://b.test", "user": "u", "password": "p"},
    ])
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    calls = []

    def call(target, route, body, **kwargs):
        calls.append((target.base_id, route, body))
        return {"methodsVersion": version, "capabilities": ["receiver_inspection"]} if route == "service/readiness" else NATIVE

    monkeypatch.setattr(server, "call_target", call)
    store = Mock(side_effect=AssertionError("Receiver observation must not create a project plan or write operation"))
    monkeypatch.setattr(server, "operation_store", store)
    result = server.inspect_project_receiver(PROJECT, "row", ["count", "sample"], ["amount"], 1, base_id="b")
    assert result is NATIVE
    assert result["checks"]["count"]["result"]["count"] == "9223372036854775807"
    assert result["sourceEqualityVerified"] is False
    assert [(base, route) for base, route, _ in calls] == [
        ("b", "service/readiness"), ("b", "projects/receiver/inspect"),
    ]
    assert calls[1][2]["sampleLimit"] == 1 and calls[1][2]["sampleFields"] == ["amount"]
    assert server._target.get() is None
    store.assert_not_called()
    assert not (tmp_path / "unused.sqlite").exists()


def test_proxy_boundary_forbids_sql_override_before_backend(monkeypatch):
    backend = Mock()
    secure_main.app.dependency_overrides[secure_main.authorize] = lambda: None
    secure_main.app.dependency_overrides[secure_main.client] = lambda: backend
    try:
        with TestClient(secure_main.app) as http:
            response = http.post("/projects/receiver/inspect", json={"projectGuid": PROJECT, "sql": "SELECT 1"})
            assert response.status_code == 422
            backend.call.assert_not_called()
            backend.call.return_value = NATIVE
            response = http.post("/projects/receiver/inspect", json={"projectGuid": PROJECT})
            assert response.status_code == 200 and response.json() == NATIVE
            backend.call.assert_called_once_with("projects/receiver/inspect", ProjectReceiverInspect(projectGuid=PROJECT).model_dump())
    finally:
        secure_main.app.dependency_overrides.clear()


@pytest.mark.parametrize("status,code", [
    (404, "RECEIVER_PROJECT_NOT_FOUND"), (404, "RECEIVER_ROW_NOT_FOUND"),
    (401, "RECEIVER_ACCESS_DENIED"), (403, "RECEIVER_ACCESS_DENIED"),
    (404, None), (405, None),
])
def test_confirmed_receiver_feature_preserves_business_or_access_error(monkeypatch, status, code):
    error_id = "3ef5c425-88c8-4457-93d8-8ae84724ef2b"
    error = ToolError(f"1C API HTTP {status}; receiver failure; code={code}; errorId={error_id}")
    backend = Mock(side_effect=[{"methodsVersion": "3.3.0", "capabilities": ["receiver_inspection"]}, error])
    monkeypatch.setattr(server, "call_api", backend)
    with pytest.raises(ToolError) as raised:
        server.inspect_project_receiver(PROJECT)
    assert raised.value is error
    assert "update" not in str(raised.value)
    assert backend.call_count == 2
    assert backend.call_args.args[0] == "projects/receiver/inspect"


@pytest.mark.parametrize("transport", ["direct_http", "dcc"])
@pytest.mark.parametrize("status,code", [(404, "RECEIVER_PROJECT_NOT_FOUND"),
                                         (404, "RECEIVER_ROW_NOT_FOUND"),
                                         (403, "RECEIVER_ACCESS_DENIED")])
def test_receiver_transports_preserve_safe_business_code_and_uuid_not_driver_text(transport, status, code):
    error_id = "3ef5c425-88c8-4457-93d8-8ae84724ef2b"
    body = {"code": code, "errorId": error_id, "error": "Provider failure; Password=private-secret"}
    with pytest.raises(ProjectsApiError) as raised:
        if transport == "direct_http":
            session = Mock()
            session.post.return_value = Mock(status_code=status, headers={})
            session.post.return_value.json.return_value = body
            api = ProjectsApiClient(ApiSettings(_env_file=None, url="https://one.test/hs/extractor-projects/v1", user="u"), session)
            api.call("projects/receiver/inspect", {"projectGuid": PROJECT})
        else:
            DccClient._result({"status": "completed", "result_ready": True,
                               "http_status": status, "result": body}, "projects/receiver/inspect")
    assert raised.value.status == status
    assert raised.value.code == code and raised.value.error_id == error_id
    assert "private-secret" not in str(raised.value) and "Password" not in str(raised.value)
