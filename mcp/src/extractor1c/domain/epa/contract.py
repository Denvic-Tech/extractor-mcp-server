"""Опубликованный контракт EPA: закрытая схема запроса для каждого маршрута."""
from typing import Any, Literal

from pydantic import Field, TypeAdapter, model_validator

from ..execution.requests import (
    ProjectExportCancel, ProjectExportCancelRef, ProjectExportRef, ProjectExportStart, ProjectExportStatus,
    ProjectReceiverInspect,
)
from ..metadata.requests import AccountAnalyticsRequest, DescribeObjectRequest
from ..projects.changes import (
    ProjectChange, ProjectFieldMappingChange, ProjectPreview, ProjectReceiverRepair, ProjectRowChange,
    ProjectRowSettingsChange, ProjectRowsAppend, ProjectUpdate,
)
from ..projects.definition import GroupProjectDefinition, ProjectDefinition, ProjectName
from ..projects.refs import ProjectScheduleRef
from ..requests import EmptyRequest, StrictRequest
from ..schedules import ProjectSchedulePreview, ProjectScheduleSet
from .routes import ROUTES


class DirectSqlRequest(StrictRequest):
    """Internal wire marker; public tool definitions never accept this override."""
    directSql: Literal[True]

    @model_validator(mode="before")
    @classmethod
    def strict_direct_marker(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("directSql") is not True:
            raise ValueError("directSql must be Boolean true")
        return value


class DirectProjectDefinition(ProjectDefinition, DirectSqlRequest):
    pass


class DirectGroupProjectDefinition(GroupProjectDefinition, DirectSqlRequest):
    pass


class DirectProjectUpdate(ProjectUpdate, DirectSqlRequest):
    pass


class DirectProjectRowsAppend(ProjectRowsAppend, DirectSqlRequest):
    pass


class DirectProjectRowChange(ProjectRowChange, DirectSqlRequest):
    pass


class DirectProjectReceiverRepair(ProjectReceiverRepair, DirectSqlRequest):
    pass


class DirectProjectRowSettingsChange(ProjectRowSettingsChange, DirectSqlRequest):
    pass


class DirectProjectFieldMappingChange(ProjectFieldMappingChange, DirectSqlRequest):
    pass


class DirectProjectStart(ProjectExportRef, DirectSqlRequest):
    # SQL starts calculate the native plan inside the operation. An explicit
    # state, when supplied by an internal caller, still requests native CAS.
    expectedProjectState: str | None = Field(default=None, min_length=1, max_length=524288)


DirectProjectChange = (DirectProjectUpdate | DirectProjectRowsAppend | DirectProjectRowChange
                       | DirectProjectReceiverRepair | DirectProjectRowSettingsChange
                       | DirectProjectFieldMappingChange)


SCHEMAS = {"metadata/list": EmptyRequest, "metadata/describe": DescribeObjectRequest,
           "service/readiness": EmptyRequest, "accounts/subconto/describe": AccountAnalyticsRequest,
           "connections/list": EmptyRequest, "projects/list": EmptyRequest,
           "projects/preview": ProjectPreview,
           "projects/create": ProjectDefinition | GroupProjectDefinition | DirectProjectDefinition | DirectGroupProjectDefinition,
           "projects/update": ProjectChange | DirectProjectChange, "projects/verify": ProjectName,
           "projects/schedule/get": ProjectScheduleRef,
           "projects/schedule/preview": ProjectSchedulePreview,
           "projects/schedule/set": ProjectScheduleSet,
           "projects/export/preview": ProjectExportRef,
           "projects/export/start": ProjectExportStart | DirectProjectStart,
           "projects/export/status": ProjectExportStatus,
           "projects/initialization/preview": ProjectExportRef,
           "projects/initialization/start": ProjectExportStart | DirectProjectStart,
           "projects/export/cancel/preview": ProjectExportCancelRef,
           "projects/export/cancel": ProjectExportCancel,
           "projects/receiver/inspect": ProjectReceiverInspect}


def validate_request(route: str, body: Any) -> dict[str, Any]:
    """Проверить тело запроса закрытой схемой маршрута и вернуть полезную нагрузку для 1С."""
    if route not in ROUTES:
        raise ValueError("Unknown API operation")
    return TypeAdapter(SCHEMAS[route]).validate_python(body).model_dump(exclude_none=True)
