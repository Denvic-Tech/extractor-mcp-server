"""Точка входа MCP-сервера «Экстрактор 1С».

Модуль — тонкий фасад. Его имя закреплено в командах запуска и образе:
``uvicorn extractor1c.mcp_server:app``, ``python -m extractor1c.mcp_server``
и ``extractor1c-mcp``. Сама реализация разложена по слоям:

* ``domain`` — закрытые схемы запросов, правила операций, контракт EPA;
* ``application`` — сценарии и порты;
* ``infrastructure`` — HTTP 1С, DCC, журнал SQLite, настройки;
* ``interface`` — инструменты MCP, HTTP-приложение, команда запуска.

Подменять зависимости в тестах следует в ``interface.mcp.runtime`` (здесь
доступен как ``runtime``): фасад только переэкспортирует имена.
"""
import time  # noqa: F401  (переэкспорт для тестов кэша возможностей)

import requests  # noqa: F401

from . import EPA_CONTRACT_VERSION, __version__  # noqa: F401
from .infrastructure.onec_http import ProjectsApiClient  # noqa: F401
from .infrastructure.settings import ApiSettings  # noqa: F401
from .interface.cli import main
from .interface.http.app import app, lifespan, mcp_app  # noqa: F401
from .interface.http.auth import authenticated_principal  # noqa: F401
from .interface.mcp import registry, runtime  # noqa: F401
from .interface.mcp.instructions import AI_SKILL_REQUIREMENTS, SERVER_INSTRUCTIONS  # noqa: F401
from .interface.mcp.registry import (  # noqa: F401
    READ, WRITE, LicenseRejectedToolError, base_tool, mcp, threaded_tool,
)
from .interface.mcp.runtime import (  # noqa: F401
    _direct_capabilities, _forget_direct_capability, _has_direct_capability, _operation_id, _operation_store,
    _read_dcc_metadata, _remember_direct_capability, _target, call_api, operation_store,
)
from .interface.mcp.tools._shared import _commit, _preview_project_definition, _write_project  # noqa: F401
from .interface.mcp.tools.execution import (  # noqa: F401
    cancel_project_export, get_project_export_status, initialize_project_queue, inspect_project_receiver,
    preview_project_export, preview_project_export_cancellation, preview_project_initialization,
    start_project_export,
)
from .interface.mcp.tools.metadata import (  # noqa: F401
    analyze_reference_graph, describe_object, describe_object_fields, get_metadata_snapshot, list_metadata,
    search_metadata,
)
from .interface.mcp.tools.operations import get_operation_status  # noqa: F401
from .interface.mcp.tools.projects import (  # noqa: F401
    create_project, get_project_plan, get_project_template_schema, list_connections, list_projects,
    preview_project, preview_project_properties, preview_project_template, update_project,
    validate_project_template, verify_project, verify_project_summary,
)
from .interface.mcp.tools.schedules import (  # noqa: F401
    get_project_schedule, preview_project_schedule, set_project_schedule,
)
from .interface.mcp.tools.service import get_skill, health, list_bases, list_skills, readiness  # noqa: F401

if __name__ == "__main__":
    main()
