"""Готовность выбранной базы: связь с 1С, версия опубликованных методов, подключения."""
import re
from typing import Any

from ..domain.identifiers import VERSION
from ..domain.projects.connections import select_connection
from .errors import ApplicationError, GatewayError
from .ports import AppContext


def readiness(ctx: AppContext, *, mcp_version: str, expected_methods_version: str,
              message: str) -> dict[str, Any]:
    # An explicit readiness call always refreshes the selected identity. A
    # missing capability or failed read must not leave an earlier positive hit.
    ctx.direct_capability.forget()
    store_ready = ctx.operations.ready()
    version_warning = None
    try:
        build = ctx.call("service/readiness", {})
    except GatewayError as exc:
        # Старая публикация не знает маршрута версии; ошибки доступа не скрываем.
        if exc.status not in {400, 404}:
            raise
        build = {"methodsVersion": None, "capabilities": []}
        version_warning = "Published 1C API does not expose its methods version; update the extension"
    if (not isinstance(build, dict)
            or (build.get("methodsVersion") is None and not version_warning)
            or (build.get("methodsVersion") is not None
                and not re.fullmatch(VERSION, str(build["methodsVersion"])))):
        raise ApplicationError("1C API did not report its methods version; update the API extension")
    if build["methodsVersion"] is not None and build["methodsVersion"] != expected_methods_version:
        version_warning = (
            f"Published 1C methods {build['methodsVersion']} differ from MCP contract {expected_methods_version}; "
            "synchronize the EPA module before project writes"
        )
    connections = ctx.call("connections/list", {})
    if isinstance(connections, list):
        count = len(connections)
    elif isinstance(connections, dict) and isinstance(connections.get("items"), list):
        count = len(connections["items"])
    else:
        count = None
    try:
        selection = select_connection(connections)
    except ValueError as exc:
        raise ApplicationError(str(exc)) from None
    ctx.direct_capability.remember(build)
    return {"ready": store_ready, "operationStore": store_ready,
            "mcpVersion": mcp_version, "expectedOneCMethodsVersion": expected_methods_version,
            "oneCMethodsVersion": build["methodsVersion"], "oneCCapabilities": build.get("capabilities", []),
            "oneCMethodsVersionWarning": version_warning,
            "connectionCount": count, "connectionSelection": selection,
            "message": message}
