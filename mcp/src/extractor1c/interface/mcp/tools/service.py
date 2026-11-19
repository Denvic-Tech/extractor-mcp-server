"""Служебные инструменты: базы, состояние сервера, готовность 1С и встроенные навыки."""
from mcp.server.fastmcp.exceptions import ToolError

import extractor1c

from ....application import readiness as readiness_scenario
from ....infrastructure.skill_catalog import get_skill_data, list_skills_data
from .. import runtime
from ..context import CONTEXT
from ..instructions import AI_SKILL_REQUIREMENTS
from ..registry import READ, base_tool, threaded_tool


@threaded_tool(annotations=READ)
def list_bases() -> list[dict]:
    """List configured direct bases or all DCC connectors accessible to this installation."""
    return runtime.bases()


@threaded_tool(annotations=READ)
def health() -> dict:
    """Check local configuration; does not test the 1C connection."""
    settings = runtime.settings()
    return {
        "configured": (bool(settings.dcc_url and settings.dcc_user and settings.dcc_password)
                       if settings.transport == "dcc"
                       else bool(settings.bases or settings.bases_file or (settings.url and settings.user))),
        "transport": settings.transport,
        "tlsVerification": not settings.allow_http_dev,
        "mutualTls": bool(settings.client_cert and settings.client_key),
        "operationStoreConfigured": bool(settings.operation_db),
    }


@threaded_tool(annotations=READ)
@base_tool
def readiness() -> dict:
    """Verify that the MCP service can reach and authenticate to the 1C API."""
    # Версии читаются из пакета при каждом вызове: номер релиза MCP и версия
    # контракта EPA независимы и не подменяют друг друга.
    return readiness_scenario.readiness(
        CONTEXT, mcp_version=extractor1c.__version__,
        expected_methods_version=extractor1c.EPA_CONTRACT_VERSION, message=AI_SKILL_REQUIREMENTS,
    )


@threaded_tool(annotations=READ)
def list_skills() -> dict:
    """List project skills from Markdown files. Call this to find skill IDs and documents to read with get_skill."""
    return list_skills_data()


@threaded_tool(annotations=READ)
def get_skill(skill_id: str, document: str = "SKILL.md") -> dict:
    """Read the full Markdown text of a project skill or one of its listed reference documents."""
    try:
        return get_skill_data(skill_id, document)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
