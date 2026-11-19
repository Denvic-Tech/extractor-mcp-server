"""Выбор подключения 1С для предпросмотра проекта."""
from ..domain.projects.connections import bind_project_connection, select_connection
from .ports import CallApi


def resolve_project_connection(payload: dict, call_api: CallApi) -> tuple[dict, dict]:
    """Resolve once before preview, binding the exact name into every plan row."""
    target = payload.get("definition", payload)
    rows = target.get("rows", [target])
    requested = target.get("connectionName", "") or next(
        (row["connectionName"] for row in rows if row.get("connectionName")), ""
    )
    current = None
    if "expectedState" in payload:
        current = call_api("projects/verify", {"projectName": payload["projectName"]}).get("connectionName")
        if not current:
            raise ValueError("Update the 1C API extension: project verification must return connectionName")
        if requested and requested != current:
            raise ValueError("Existing project connection cannot be changed by a row update")
        requested = current
    selection = select_connection(call_api("connections/list", {}), requested)
    # Repair is bound to the saved connection, but its closed native payload
    # has no connectionName: adding it would change the five-key contract.
    if selection["status"] == "selected" and "receiverRepair" not in payload:
        bind_project_connection(payload, selection["connectionName"])
    return payload, selection
