"""Фиксированные маршруты опубликованного API 1С (EPA). Произвольного кода среди них нет."""


ROUTES = frozenset({"metadata/list", "metadata/describe", "connections/list", "projects/list",
                    "service/readiness", "accounts/subconto/describe",
                    "projects/preview", "projects/create", "projects/update", "projects/verify",
                    "projects/schedule/get", "projects/schedule/preview", "projects/schedule/set",
                    "projects/export/preview", "projects/export/start", "projects/export/status",
                    "projects/initialization/preview", "projects/initialization/start",
                    "projects/export/cancel/preview", "projects/export/cancel",
                    "projects/receiver/inspect"})

# Изменяющее действие MCP и единственный маршрут EPA, которым оно выполняется.
ACTION_ROUTES = {
    "create": "projects/create",
    "update": "projects/update",
    "schedule": "projects/schedule/set",
    "export": "projects/export/start",
    "initialization": "projects/initialization/start",
    "cancel": "projects/export/cancel",
}

# Маршруты, для которых EPA может подтвердить отказ до любой записи.
LICENSED_WRITE_ROUTES = frozenset({"projects/create", "projects/update", "projects/schedule/set"})
GUARDED_WRITE_ROUTES = frozenset({"projects/create", "projects/update", "projects/export/start",
                                  "projects/initialization/start"})
