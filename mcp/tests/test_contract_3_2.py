from extractor1c import __version__
from extractor1c.mcp_server import app, mcp
from extractor1c.projects_api import ProjectDefinition, GroupProjectDefinition


def test_server_versions_are_synchronized():
    assert __version__ == app.version == mcp._mcp_server.version == "3.2.0"


def test_nested_rows_use_the_same_array_serialization():
    row = ProjectDefinition(object="Справочник.Контрагенты", projectName="p", tableName="t", segmentation=[
        {"field": "Ссылка", "segment": "По значению", "regular": True, "extended": True, "initialization": True}])
    body = GroupProjectDefinition(projectName="p", rows=[row]).model_dump(exclude_none=True)
    assert "segment" not in body["rows"][0] and "segmentField" not in body["rows"][0]


def test_direct_and_dcc_send_identical_canonical_array_payload():
    from unittest.mock import Mock
    from uuid import uuid4
    from extractor1c.projects_api import ApiSettings, ProjectsApiClient
    from extractor1c.transport import DccClient
    operation = str(uuid4())
    body = {"object": "РегистрБухгалтерии.Хозрасчетный", "projectName": "p", "connectionName": "receiver", "tableName": "t", "periodicity": "Регистратор", "segmentation": [
        {"field": "Период", "segment": "День", "regular": True, "extended": False, "initialization": True}]}
    session = Mock()
    session.post.return_value = Mock(status_code=200, headers={})
    session.post.return_value.json.return_value = {"ok": True}
    direct = ProjectsApiClient(ApiSettings(_env_file=None, url="https://one.test/hs/extractor-projects/v1", user="u"), session)
    direct.call("projects/preview", body)
    expected = session.post.call_args.kwargs["json"]
    assert expected["periodicity"] == "Регистратор"
    assert "segment" not in expected and "segmentField" not in expected
    api = DccClient(ApiSettings(_env_file=None, transport="dcc", dcc_url="https://dcc.test", dcc_user="u", dcc_password="p"))
    api.token = "test-token"
    api._request = Mock(side_effect=[{"data": {"operation_id": operation, "task_id": operation, "status": "queued"}},
                                    {"data": {"operation_id": operation, "task_id": operation, "status": "completed", "result_ready": True, "http_status": 200, "result": {"ok": True}}}])
    assert api.call("base", "projects/preview", body, operation) == {"ok": True}
    assert api._request.call_args_list[0].kwargs["json"]["payload"] == expected
    api.session.close()
