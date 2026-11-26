"""Ошибки EPA: подтверждённые отказы до записи и безопасная публичная диагностика."""
from typing import Any

from ..identifiers import is_uuid
from .routes import GUARDED_WRITE_ROUTES, LICENSED_WRITE_ROUTES


LICENSE_ERRORS = {
    "LICENSE_REQUIRED": (403, "Требуется лицензия Экстрактора 1С."),
    "LICENSE_INVALID": (403, "Лицензия Экстрактора 1С не прошла штатную проверку."),
    "SUPPORT_REQUIRED": (403, "Для записи проекта требуется действующий срок поддержки dateDue."),
    "SUPPORT_EXPIRED": (403, "Срок поддержки лицензии истек. Продлите поддержку для записи проекта."),
    "LICENSE_CHECK_UNAVAILABLE": (503, "Не удалось подтвердить право записи через штатные методы лицензирования."),
}

PREWRITE_ERRORS = {
    **LICENSE_ERRORS,
    "PROJECT_CREATE_BUSY": (409, "Project creation lock could not be acquired; no project or receiver changes were made. Inspect error details before retry."),
    "DIRECT_SQL_RECEIVER_REQUIRED": (422, "Direct operations require a native SQL receiver; use preview for message receivers."),
    "DIRECT_SQL_STATE_CHANGED": (412, "Native project or SQL receiver changed before the operation; verify current state."),
}


PUBLIC_API_ERRORS = {
    "OPERATION_FAILED": "1C operation failed; verify project and receiver state before retry",
    **{code: "Field mapping change failed; verify saved mapping and receiver schema before retry"
       for code in (
           "FIELD_MAPPING_STATE_CONFLICT", "FIELD_MAPPING_RECEIVER_UNSUPPORTED",
           "FIELD_MAPPING_TABLE_ABSENT", "FIELD_MAPPING_SOURCE_UNKNOWN_OR_AMBIGUOUS",
           "FIELD_MAPPING_SEGMENT_REQUIRED", "FIELD_MAPPING_TARGET_TOO_LONG",
           "FIELD_MAPPING_TARGET_EXISTS", "FIELD_MAPPING_COLUMN_ABSENT",
           "FIELD_MAPPING_COLUMN_TYPE_CONFLICT", "FIELD_MAPPING_TARGET_DUPLICATE",
           "FIELD_MAPPING_DDL_INDETERMINATE",
       )},
    "RECEIVER_PROJECT_NOT_FOUND": "Receiver inspection project not found",
    "RECEIVER_ROW_NOT_FOUND": "Receiver inspection project row not found",
    "RECEIVER_ROW_SELECTION_REQUIRED": "Select a saved project row for receiver inspection",
    "RECEIVER_ACCESS_DENIED": "Receiver inspection access denied",
    "RECEIVER_ADDRESS_UNSUPPORTED": "Saved receiver address is unsupported",
    "RECEIVER_NON_SQL_UNSUPPORTED": "Receiver inspection requires a supported SQL receiver",
    "RECEIVER_REQUEST_INVALID": "Invalid receiver inspection request",
    "RECEIVER_SAMPLE_FIELD_UNKNOWN": "Sample field was not found in the saved mapping",
    "RECEIVER_SAMPLE_FIELD_AMBIGUOUS": "Sample field is ambiguous in the saved mapping",
    "RECEIVER_SAMPLE_TYPE_UNSUPPORTED": "Mapped sample field type is unsupported",
    "RECEIVER_RESPONSE_TOO_LARGE": "Receiver inspection response exceeds the bounded limit",
    "RECEIVER_TABLE_ABSENT": "Saved receiver table does not exist",
    "RECEIVER_QUERY_FAILED": "Receiver observation failed; inspect the diagnostic identifier",
    "RECEIVER_INSPECTION_FAILED": "Receiver inspection failed; inspect the diagnostic identifier",
    "SQL_ROW_EXECUTION_UNRESOLVED": "Project execution is active or unknown; resolve the original task before changing columns",
    "SQL_ROW_DDL_INDETERMINATE": "SQL columns may have changed; reconcile the schema and project before retry",
    "SQL_ROW_COLUMN_CONFLICT": "Receiver columns conflict with the requested project mapping",
    "SQL_ROW_SCHEMA_UNAVAILABLE": "Receiver schema could not be verified; no column change was approved",
    "MYSQL_RECEIVER_TEXT_COLUMN_UNSUPPORTED": "Selected receiver column does not support the requested TEXT mapping",
    "MYSQL_RECEIVER_TABLE_EXISTS": "Receiver table already exists; missing-table repair refused",
    "MYSQL_RECEIVER_CREATE_FAILED": "Receiver table creation failed; inspect the diagnostic identifier and receiver schema before retry",
    "MYSQL_RECEIVER_REPAIR_UNSUPPORTED": "Saved MySQL receiver repair is unsupported",
    "PERIOD_FIELD_UNAVAILABLE": "Period field is unavailable; choose source periodicity or another segment field",
    "PROJECT_NOT_FOUND": "Project not found",
    "PROJECT_CHANGED": "Project changed; preview again",
    "SCHEMA_CHANGE_UNSUPPORTED": "Column changes for this receiver must be configured in 1C",
    "MULTI_ROW_UPDATE_UNSUPPORTED": "This operation does not support multiple project rows",
    "GUID_COLUMN_REQUIRED": "The selected source field must be a GUID column",
    "FIELD_NOT_FOUND": "The requested field was not found",
}


class ProjectsApiError(RuntimeError):
    def __init__(self, status, message, *, code=None, write_applied=None, error_id=None,
                 error_details: dict[str, str | bool] | None = None):
        self.status = status
        self.code = code
        self.write_applied = write_applied
        self.error_id = error_id
        self.error_details = error_details
        super().__init__(message)

    @property
    def rejected_before_write(self):
        return (self.code in PREWRITE_ERRORS and self.write_applied is False
                and self.status == PREWRITE_ERRORS[self.code][0])

    def rejection(self):
        return {"httpStatus": self.status, "code": self.code,
                "error": str(self), "writeApplied": False,
                **({"errorId": self.error_id} if self.error_id else {}),
                **(self.error_details or {})}


def public_error_details(result: Any) -> dict[str, str | bool] | None:
    """Accept explicit EPA diagnostics, hiding sensitive text before truncation."""
    if not isinstance(result, dict):
        return None
    text = result.get("errorDetails")
    redacted = result.get("errorDetailsRedacted")
    truncated = result.get("errorDetailsTruncated")
    if not isinstance(text, str) or type(redacted) is not bool or type(truncated) is not bool:
        return None
    lower = " ".join(text.casefold().split())
    markers = (
        "password", "пароль", "pwd", "token", "токен", "apikey", "api_key",
        "secret", "секрет", "authorization", "bearer ", "://", "connection string",
        "строка соединения", "server=", "host=", "database=", "user id=", "uid=",
        "srvr=", "ref=", "usr=", "select ", "insert ", "update ", "delete ",
        "drop ", "alter ", "create table", "values", "выбрать ", "'", '"',
        "connection", "соединени", "подключени", "dsn", "user=", "вызватьисключение",
        "параметры", "parameters", "payload", "request body", "тело запроса", "sql", "=",
    )
    redacted = redacted or any(marker in lower for marker in markers)
    return {
        "errorDetails": "[REDACTED: sensitive native error]" if redacted else text[:4000],
        "errorDetailsRedacted": redacted,
        "errorDetailsTruncated": truncated or len(text) > 4000,
    }


def public_api_error(status: int, result: Any, default_message: str) -> ProjectsApiError:
    """Preserve published reasons, UUIDs and explicitly sanitized EPA details."""
    error_id = result.get("errorId") if isinstance(result, dict) else None
    if not is_uuid(error_id):
        error_id = None
    code = result.get("code") if isinstance(result, dict) else None
    if not isinstance(code, str) or code not in PUBLIC_API_ERRORS:
        code = None
    return ProjectsApiError(status, PUBLIC_API_ERRORS.get(code, default_message),
                            code=code, error_id=error_id, error_details=public_error_details(result))


class DccOperationPending(ProjectsApiError):
    """Waiting expired or submission outcome is unknown; resume the same ID."""
    def __init__(self, operation_id: str, task_id: str | None = None):
        self.operation_id = operation_id
        self.task_id = task_id
        super().__init__(202, "DCC operation pending; query the same operation ID before retry")


def upstream_failure(route: str, status: int, result: Any, default_message: str) -> ProjectsApiError:
    """Классифицировать отказ 1С одинаково для прямого HTTP и для результата DCC.

    Подтверждённым отказом до записи считается только известный код с точным
    HTTP-статусом и ``writeApplied: false`` на маршруте, где EPA его публикует.
    Произвольный текст 1С наружу не передаётся.
    """
    code = result.get("code") if isinstance(result, dict) else None
    known = PREWRITE_ERRORS.get(code) if isinstance(code, str) else None
    if known is not None and status == known[0] and result.get("writeApplied") is False:
        allowed = LICENSED_WRITE_ROUTES if code in LICENSE_ERRORS else GUARDED_WRITE_ROUTES
        if route in allowed:
            diagnostic = public_api_error(status, result, known[1])
            return ProjectsApiError(status, known[1], code=code, write_applied=False,
                                    error_id=diagnostic.error_id, error_details=diagnostic.error_details)
    return public_api_error(status, result, default_message)
