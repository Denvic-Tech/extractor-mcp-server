"""Публичный контракт MCP: имена, параметры и аннотации инструментов неизменны.

Снимок хранит устойчивую проекцию контракта (без деталей генерации JSON Schema),
поэтому не зависит от версии pydantic. Осознанное изменение контракта:

    EXTRACTOR_UPDATE_TOOL_CONTRACT=1 python -m pytest -q tests/test_tool_contract.py
"""
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path

from extractor1c import mcp_server as server

SNAPSHOT = Path(__file__).parent / "fixtures" / "tool_contract.json"


def current_contract() -> dict:
    tools = asyncio.run(server.mcp.list_tools())
    return {
        "serverName": server.mcp.name,
        "instructionsSha256": sha256(server.mcp.instructions.encode("utf-8")).hexdigest(),
        "tools": {
            tool.name: {
                "description": tool.description,
                "annotations": tool.annotations.model_dump(exclude_none=True),
                "parameters": sorted(tool.inputSchema.get("properties", {})),
                "required": sorted(tool.inputSchema.get("required", [])),
                "forbidsUnknown": tool.inputSchema.get("additionalProperties") is False,
            }
            for tool in tools
        },
    }


def test_tool_contract_matches_snapshot():
    contract = current_contract()
    if os.environ.get("EXTRACTOR_UPDATE_TOOL_CONTRACT") == "1":
        SNAPSHOT.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert sorted(contract["tools"]) == sorted(expected["tools"])
    for name, tool in expected["tools"].items():
        assert contract["tools"][name] == tool, name
    assert contract["serverName"] == expected["serverName"]
    assert contract["instructionsSha256"] == expected["instructionsSha256"]
