"""Offline contract tests; DCC relay endpoints require the specified deployment."""
from uuid import uuid4

import pytest
import requests

from extractor1c.projects_api import ApiSettings, ProjectsApiError
from extractor1c.transport import DccClient, DccOperationPending, list_bases, resolve_target


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


class Session:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return Response(response)


def settings(**overrides):
    return ApiSettings(_env_file=None, transport="dcc", dcc_url="https://dcc.test", dcc_user="user",
                       dcc_password="secret", dcc_wait_timeout=0, **overrides)


def client(responses):
    session = Session([{"data": {"access_token": "token"}}, *responses])
    return DccClient(settings(), session=session), session


def test_direct_bases_selection_and_no_credentials_disclosed():
    config = ApiSettings(_env_file=None, bases=[
        {"base_id": "one", "name": "First", "url": "https://one/hs/extractor-projects/v1", "password": "private"},
        {"base_id": "two", "name": "Second", "url": "https://two/hs/extractor-projects/v1"}])
    assert list_bases(config) == [{"baseId": "one", "name": "First", "transport": "direct_http"},
                                  {"baseId": "two", "name": "Second", "transport": "direct_http"}]
    with pytest.raises(ValueError, match="Select base_id"):
        resolve_target(config)
    first, second = resolve_target(config, "one"), resolve_target(config, "two")
    assert first.settings.password == "private"
    assert first.identity != second.identity
    with pytest.raises(ValueError, match="Unknown"):
        resolve_target(config, "missing")


def test_legacy_default_target_does_not_validate_url_before_call():
    assert resolve_target(ApiSettings(_env_file=None)).base_id == "default"


def test_duplicate_base_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        list_bases(ApiSettings(_env_file=None, bases=[{"base_id": "x"}, {"base_id": "x"}]))


def test_dcc_paging_keeps_offline_connectors_and_uses_only_dcc():
    api, session = client([
        {"data": [{"id_connector": "11111111-1111-4111-8111-111111111111", "name": "First", "last_status": "offline", "client_contract": "extractor-1c/2.0", "supported_commands": ["mcp"]}], "meta": {"total": 2}},
        {"data": [{"id_connector": "22222222-2222-4222-8222-222222222222", "name": "Second", "client_contract": "extractor-1c/2.0", "supported_commands": ["mcp"]}], "meta": {"total": 2}},
    ])
    assert [base["baseId"] for base in api.list_bases()] == ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]
    assert session.calls[2][2]["params"]["offset"] == 1
    assert all(url.startswith("https://dcc.test/api/") for _, url, _ in session.calls)
    assert all(call[2]["allow_redirects"] is False for call in session.calls)


def test_dcc_result_roundtrip_stable_id_and_payload():
    operation = str(uuid4())
    api, session = client([{"data": {"operation_id": operation, "task_id": operation, "status": "queued"}},
                           {"data": {"operation_id": operation, "task_id": operation, "status": "completed", "result_ready": True,
                                     "http_status": 200, "result": [{"name": "x"}]}}])
    assert api.call("base", "metadata/list", {}, operation) == [{"name": "x"}]
    assert session.calls[1][2]["headers"]["Idempotency-Key"] == operation
    assert session.calls[1][2]["json"] == {"operation_id": operation, "method": "metadata/list", "payload": {}}


def test_timeout_preserves_operation_id_and_does_not_resubmit():
    operation = str(uuid4())
    api, session = client([{"data": {"operation_id": operation, "task_id": operation, "status": "queued"}}, {"data": {"operation_id": operation, "task_id": operation, "status": "queued"}}])
    with pytest.raises(DccOperationPending) as caught:
        api.call("base", "metadata/list", {}, operation)
    assert caught.value.operation_id == operation
    assert sum(method == "POST" and url.endswith("/operations") for method, url, _ in session.calls) == 1


def test_submission_transport_failure_carries_same_operation_id():
    operation = str(uuid4())
    api, _ = client([requests.ConnectionError("secret must not leak")])
    with pytest.raises(DccOperationPending) as caught:
        api.call("base", "metadata/list", {}, operation)
    assert caught.value.operation_id == operation
    assert "secret" not in str(caught.value)


def test_terminal_status_without_ready_result_is_pending():
    operation = str(uuid4())
    api, _ = client([{"data": {"operation_id": operation, "task_id": operation, "status": "queued"}}, {"data": {"operation_id": operation, "task_id": operation, "status": "completed", "result_ready": False}}])
    with pytest.raises(DccOperationPending):
        api.call("base", "metadata/list", {}, operation)


def test_operation_identity_is_verified():
    api, _ = client([{"data": {"operation_id": str(uuid4()), "status": "queued"}}])
    with pytest.raises(ProjectsApiError, match="identity"):
        api.get_operation("base", str(uuid4()))


def test_license_rejection_retains_safe_semantics():
    operation = str(uuid4())
    state = {"operation_id": operation, "task_id": operation, "status": "failed", "result_ready": True, "http_status": 403,
             "result": {"code": "LICENSE_REQUIRED", "writeApplied": False, "error": "private server text"}}
    with pytest.raises(ProjectsApiError) as caught:
        DccClient._result(state, "projects/update")
    assert caught.value.rejected_before_write
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("url", ["http://dcc.test", "https://user:secret@dcc.test", "https://dcc.test/other", "https://dcc.test?token=secret"])
def test_dcc_url_rejects_unsafe_configuration(url):
    with pytest.raises(ValueError):
        DccClient(ApiSettings(_env_file=None, dcc_url=url, dcc_user="x", dcc_password="x"))


def test_routes_and_closed_request_schema_are_enforced_before_network():
    api, session = client([])
    with pytest.raises(ValueError):
        api.call("base", "execute", {})
    with pytest.raises(ValueError):
        api.call("base", "metadata/list", {"code": "arbitrary"})
    assert not session.calls


def test_success_false_is_not_accepted_as_empty_connector_list():
    api, _ = client([{"success": False, "data": [], "error": None}])
    with pytest.raises(ProjectsApiError, match="unsuccessful"):
        api.list_bases()


def test_wrong_submission_identity_is_pending_under_original_id():
    operation = str(uuid4())
    api, _ = client([{"data": {"operation_id": str(uuid4()), "task_id": operation, "status": "queued"}}])
    with pytest.raises(DccOperationPending) as caught:
        api.call("base", "metadata/list", {}, operation)
    assert caught.value.operation_id == operation


def test_operation_method_mismatch_is_rejected():
    operation = str(uuid4())
    api, _ = client([{"data": {"operation_id": operation, "task_id": operation, "status": "queued"}},
                     {"data": {"operation_id": operation, "task_id": operation, "status": "completed",
                               "method": "projects/list", "result_ready": True, "http_status": 200, "result": {}}}])
    with pytest.raises(ProjectsApiError, match="method mismatch"):
        api.call("base", "metadata/list", {}, operation)


def test_yaml_bases_keep_selection_and_credentials_private(tmp_path):
    import yaml
    path = tmp_path / "bases.yaml"
    path.write_text(yaml.safe_dump({"bases": [
        {"base_id": "erp", "url": "https://erp.test/hs/extractor-projects/v1", "user": "u", "password": "private"},
        {"base_id": "bu", "url": "https://bu.test/hs/extractor-projects/v1", "user": "u", "password": ""}]}))
    config = ApiSettings(_env_file=None, bases_file=str(path))
    assert [item["baseId"] for item in list_bases(config)] == ["erp", "bu"]
    assert "private" not in str(list_bases(config))
    assert resolve_target(config, "erp").settings.password == "private"
    with pytest.raises(ValueError, match="Select base_id"):
        resolve_target(config)
    with pytest.raises(ValueError, match="not both"):
        list_bases(config.model_copy(update={"bases": [{"base_id": "x"}]}))


@pytest.mark.parametrize("content", ["bases: []", "bases: [1]", "bases: {}", "wrong: []", "bases: ["])
def test_invalid_yaml_does_not_fall_back_to_single_base(tmp_path, content):
    path = tmp_path / "bases.yaml"
    path.write_text(content)
    with pytest.raises(ValueError):
        list_bases(ApiSettings(_env_file=None, bases_file=str(path)))


def test_dcc_discovery_filters_non_v2_or_non_mcp_connectors():
    api, _ = client([{"data": [
        {"id_connector": "11111111-1111-4111-8111-111111111111", "client_contract": "extractor-1c/2.0", "supported_commands": ["mcp"], "last_status": "offline"},
        {"id_connector": "22222222-2222-4222-8222-222222222222", "client_contract": "extractor-1c/1.0", "supported_commands": ["mcp"]},
        {"id_connector": "33333333-3333-4333-8333-333333333333", "client_contract": "extractor-1c/2.0", "supported_commands": ["update"]}], "meta": {"total": 3}}])
    rows = api.list_bases()
    assert len(rows) == 1
    assert rows[0]["status"] == "offline"


def test_dcc_login_uses_oauth_password_form_and_reuses_token():
    api, session = client([])
    first = api._headers()
    assert first == {"Authorization": "Bearer token", "Accept": "application/json"}
    assert api._headers() == first
    assert len(session.calls) == 1
    method, url, options = session.calls[0]
    assert method == "POST" and url == "https://dcc.test/api/v2/users/login"
    assert options["data"] == {"grant_type": "password", "username": "user", "password": "secret"}
    assert "json" not in options
    assert options["allow_redirects"] is False
