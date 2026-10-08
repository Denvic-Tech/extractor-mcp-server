"""Bearer-token guard on the MCP endpoint.

These tests exercise the rejection paths (401/503) and the open /health probe.
They deliberately avoid the TestClient context manager: the MCP session manager
may only start once per process (see test_mcp_server), and none of these paths
reach the mounted MCP app, so no lifespan startup is required. The authenticated
happy path is covered in test_mcp_server.py.
"""
import base64
from unittest.mock import patch

from fastapi.testclient import TestClient

from extractor1c.mcp_auth import parse_tokens
from extractor1c.mcp_server import app

ACCEPT = "application/json, text/event-stream"


def bearer(token: str) -> str:
    return "Bearer " + token


def rpc_request():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    }}


def test_parse_tokens_variants():
    assert parse_tokens("") == set()
    assert parse_tokens("  ") == set()
    # A bare entry is the token itself.
    assert parse_tokens("token-only") == {"token-only"}
    # A "label:token" entry matches only on the token part.
    assert parse_tokens("alice:secret, bob:p2 ") == {"secret", "p2"}
    # Bare and labelled entries can be mixed.
    assert parse_tokens("token-only, ci:token-2") == {"token-only", "token-2"}
    # Empty entries are ignored.
    assert parse_tokens("ok, ,") == {"ok"}


def test_health_is_open_without_tokens():
    with patch("extractor1c.mcp_auth.current_tokens", return_value=set()):
        assert TestClient(app).get("/health").status_code == 200


def test_missing_header_is_rejected():
    with patch("extractor1c.mcp_auth.current_tokens", return_value={"tok"}):
        resp = TestClient(app).post("/mcp", json=rpc_request(), headers={"Accept": ACCEPT})
        assert resp.status_code == 401
        assert resp.headers["www-authenticate"].startswith("Bearer")


def test_wrong_token_is_rejected():
    with patch("extractor1c.mcp_auth.current_tokens", return_value={"tok"}):
        resp = TestClient(app).post("/mcp", json=rpc_request(),
                                    headers={"Accept": ACCEPT, "Authorization": bearer("wrong")})
        assert resp.status_code == 401


def test_basic_scheme_is_rejected():
    # A leftover Basic header must not be accepted now that the scheme is Bearer.
    with patch("extractor1c.mcp_auth.current_tokens", return_value={"tok"}):
        basic = "Basic " + base64.b64encode(b"tok:tok").decode("ascii")
        resp = TestClient(app).post("/mcp", json=rpc_request(),
                                    headers={"Accept": ACCEPT, "Authorization": basic})
        assert resp.status_code == 401


def test_unconfigured_tokens_return_503():
    with patch("extractor1c.mcp_auth.current_tokens", return_value=set()):
        resp = TestClient(app).post("/mcp", json=rpc_request(),
                                    headers={"Accept": ACCEPT, "Authorization": bearer("tok")})
        assert resp.status_code == 503
