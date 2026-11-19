"""Инструмент чтения состояния операции записи."""
from typing import Any

from ....application import operation_status
from ....application.responses import operation_response
from ..context import CONTEXT
from ..registry import READ, base_tool, threaded_tool


@threaded_tool(annotations=READ)
@base_tool
def get_operation_status(operation_id: str) -> Any:
    """Read a durable write-operation status without retrying the write."""
    return operation_response(CONTEXT, operation_status.operation_status(CONTEXT, operation_id))
