"""Isolation and conservative asynchronous reconciliation of selected bases."""
import inspect
from uuid import uuid4

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from extractor1c import mcp_server as server
from extractor1c.mcp_auth import authenticated_principal
from extractor1c.projects_api import ApiSettings
from extractor1c.transport import DccOperationPending, Target, resolve_target


def settings(tmp_path):
    return ApiSettings(_env_file=None, operation_db=str(tmp_path / "operations.sqlite"),
                       bases=[{"base_id": "a", "url": "https://a.test", "user": "u", "password": "p"},
                              {"base_id": "b", "url": "https://b.test", "user": "u", "password": "p"}])


def test_base_selection_is_explicit_and_routes_all_nested_calls(tmp_path, monkeypatch):
    config = settings(tmp_path)
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    calls = []
    monkeypatch.setattr(server, "call_target", lambda target, route, body, **kwargs:
                        calls.append((target.base_id, route)) or [])
    with pytest.raises(ToolError, match="choose an accessible"):
        server.list_projects()
    assert server.list_projects(base_id="b") == []
    assert calls == [("b", "projects/list")]
    assert server._target.get() is None
    assert "base_id" in inspect.signature(server.list_projects).parameters


def test_store_is_partitioned_by_base_and_authenticated_principal(tmp_path):
    config = settings(tmp_path)
    a, b = resolve_target(config, "a"), resolve_target(config, "b")
    original_settings = server.ApiSettings
    server.ApiSettings = lambda **kwargs: config
    try:
        paths = []
        for target, principal in [(a, "client1"), (b, "client1"), (a, "client2")]:
            t = server._target.set(target)
            p = authenticated_principal.set(principal)
            try:
                paths.append(server.operation_store().path)
            finally:
                authenticated_principal.reset(p)
                server._target.reset(t)
        assert len(set(paths)) == 3
    finally:
        server.ApiSettings = original_settings


def test_legacy_stdio_store_does_not_follow_a_changed_url_or_user(tmp_path, monkeypatch):
    config = ApiSettings(_env_file=None, operation_db=str(tmp_path / "legacy.sqlite"),
                         url="https://first.test", user="one", password="secret")
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    paths = []
    for url, user in [("https://first.test", "one"), ("https://second.test", "one"),
                      ("https://first.test", "two")]:
        config = config.model_copy(update={"url": url, "user": user})
        token = server._target.set(resolve_target(config))
        try:
            paths.append(server.operation_store().path)
        finally:
            server._target.reset(token)
    assert len(set(paths)) == 3
    assert str(tmp_path / "legacy.sqlite") not in paths


def test_dcc_uncertain_write_recovers_same_operation_without_resubmission(tmp_path, monkeypatch):
    config = ApiSettings(_env_file=None, transport="dcc", operation_db=str(tmp_path / "ops.sqlite"))
    target = Target(config, "connector", "target-identity")
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    monkeypatch.setattr(server, "resolve_target", lambda *args: target)
    token = server._target.set(target)
    try:
        store = server.operation_store()
        plan = store.create_plan("create", {"name": "project"}, {"valid": True}, 900)
    finally:
        server._target.reset(token)
    operation_id = str(uuid4())
    submitted = []
    def pending(selected, route, body, operation_id=None):
        submitted.append(operation_id)
        raise DccOperationPending(operation_id)
    monkeypatch.setattr(server, "call_target", pending)
    with pytest.raises(ToolError, match=operation_id):
        server.create_project(plan.plan_id, operation_id, base_id="connector")
    monkeypatch.setattr(server, "get_target_operation", lambda selected, identity:
                        {"operation_id": identity, "status": "completed", "result_ready": True,
                         "http_status": 200, "method": "projects/create", "result": {"created": True}})
    result = server.get_operation_status(operation_id, base_id="connector")
    assert result["status"] == "completed"
    replay = server.create_project(plan.plan_id, operation_id, base_id="connector")
    assert replay["replayed"] is True
    assert submitted == [operation_id]


def test_preview_plan_cannot_be_read_from_another_base(tmp_path, monkeypatch):
    config = settings(tmp_path)
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    token = server._target.set(resolve_target(config, "a"))
    try:
        plan = server.operation_store().create_plan("create", {"name": "x"}, {}, 900)
    finally:
        server._target.reset(token)
    with pytest.raises(ToolError, match="Plan not found"):
        server.get_project_plan(plan.plan_id, base_id="b")


def test_pending_read_is_resumable_only_by_the_same_principal(tmp_path, monkeypatch):
    config = ApiSettings(_env_file=None, transport="dcc", operation_db=str(tmp_path / "ops.sqlite"))
    target = Target(config, "connector", "target")
    monkeypatch.setattr(server, "ApiSettings", lambda **kwargs: config)
    monkeypatch.setattr(server, "resolve_target", lambda *args: target)
    operation_id = str(uuid4())
    def pending(*args, **kwargs):
        raise DccOperationPending(operation_id)
    monkeypatch.setattr(server, "call_target", pending)
    principal = authenticated_principal.set("owner")
    try:
        with pytest.raises(ToolError, match=operation_id):
            server.list_projects(base_id="connector")
        monkeypatch.setattr(server, "get_target_operation", lambda *args:
                            {"status": "completed", "result_ready": True, "http_status": 200,
                             "method": "projects/list", "result": []})
        assert server.get_operation_status(operation_id, base_id="connector")["result"] == []
    finally:
        authenticated_principal.reset(principal)
    principal = authenticated_principal.set("other")
    try:
        with pytest.raises(ToolError, match="Operation not found"):
            server.get_operation_status(operation_id, base_id="connector")
    finally:
        authenticated_principal.reset(principal)
