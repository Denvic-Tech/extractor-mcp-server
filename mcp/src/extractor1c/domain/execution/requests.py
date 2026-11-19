"""Запуск, состояние, диагностика и отмена выгрузки; чтение приёмника сохранённой строки."""
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, StrictBool, StrictInt, model_serializer, model_validator

from ..identifiers import UUID_PATTERN, has_control_characters
from ..projects.refs import ProjectScheduleRef
from ..requests import StrictRequest


class ProjectExportRef(ProjectScheduleRef):
    """An existing project, never executable code or an arbitrary HTTP URL."""


ReceiverCheck = Literal["schema", "count", "sample"]


class ProjectReceiverInspect(ProjectExportRef):
    """Read a receiver bound to a saved project row; no connection or SQL overrides."""
    rowIdentifier: str = ""
    checks: list[ReceiverCheck] = Field(default_factory=lambda: ["schema"], min_length=1)
    sampleFields: list[str] = Field(default_factory=list, max_length=64)
    sampleLimit: StrictInt = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def valid_receiver_checks(self):
        if len(set(self.checks)) != len(self.checks):
            raise ValueError("Duplicate receiver checks")
        if (self.rowIdentifier and not self.rowIdentifier.strip()) or has_control_characters(
            self.rowIdentifier
        ):
            raise ValueError("Invalid project row identifier")
        if any(not name.strip() or has_control_characters(name) for name in self.sampleFields):
            raise ValueError("Invalid mapped sample field name")
        if len(set(self.sampleFields)) != len(self.sampleFields):
            raise ValueError("Duplicate sample fields")
        if "sample" in self.checks:
            if not self.sampleFields or self.sampleLimit == 0:
                raise ValueError("Sample requires explicit fields and sampleLimit 1..100")
        elif self.sampleFields or self.sampleLimit != 0:
            raise ValueError("Sample fields and limit require the sample check")
        return self


class ProjectExportStart(ProjectExportRef):
    expectedProjectState: str = Field(min_length=1, max_length=524288)


class ProjectExportStatus(ProjectExportRef):
    taskId: str | None = Field(default=None, pattern=UUID_PATTERN)
    includeDiagnostics: StrictBool = False

    @model_validator(mode="after")
    def diagnostics_require_task(self) -> "ProjectExportStatus":
        if self.includeDiagnostics and self.taskId is None:
            raise ValueError("Query diagnostics require an explicit taskId")
        return self

    @model_serializer(mode="wrap")
    def compatible_status_payload(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload = handler(self)
        # Older EPA publications reject unknown arguments. Ordinary status must
        # retain its exact previous wire shape, even when false was explicit.
        if not self.includeDiagnostics:
            payload.pop("includeDiagnostics", None)
        return payload


class QueryDiagnosticEvent(StrictRequest):
    """Native history text, with native timestamp units and explicit redaction."""
    atMs: StrictInt
    order: StrictInt
    rowIdentifier: str
    table: str
    partition: str
    kind: Literal["query_text", "query_parameters", "serialized_parameters", "error"]
    text: str = Field(max_length=4000)
    textTruncated: StrictBool
    textRedacted: StrictBool


class ProjectQueryDiagnostics(StrictRequest):
    """Bounded project history is approximate evidence, never a complete trace."""
    available: StrictBool
    scope: Literal["project_history"]
    correlation: Literal["time_window", "unavailable"]
    complete: StrictBool
    parametersFormat: Literal["native_log_text"]
    events: list[QueryDiagnosticEvent] = Field(max_length=100)
    warnings: list[str]
    truncated: StrictBool

    @model_validator(mode="after")
    def history_is_incomplete(self) -> "ProjectQueryDiagnostics":
        if self.complete:
            raise ValueError("Project history cannot claim a complete task trace")
        if self.available and (self.correlation != "time_window" or not self.events):
            raise ValueError("Available diagnostics require time-correlated history events")
        if self.correlation == "unavailable" and self.events:
            raise ValueError("Uncorrelated history events cannot describe the selected task")
        return self


class ProjectExportCancelRef(ProjectExportRef):
    taskId: str = Field(pattern=UUID_PATTERN)


class ProjectExportCancel(ProjectExportCancelRef):
    expectedTaskState: str = Field(min_length=1, max_length=524288)
