"""Изменения сохранённого проекта: свойства, строки, сопоставление полей, ремонт приёмника."""
import re
from typing import Literal

from pydantic import Field, SerializerFunctionWrapHandler, StrictBool, model_serializer, model_validator

from ..identifiers import BOUNDED_LATIN_NAME, FIELD_PATH_PATTERN, LATIN_NAME_PATTERN
from ..requests import StrictRequest
from .definition import (
    GroupProjectDefinition, ProjectConnection, ProjectDefinition, ProjectName, RowSettings, require_consistent_rows,
)


class ProjectUpdate(ProjectName):
    expectedState: str = Field(min_length=1, max_length=524288)
    comment: str | None = Field(default=None, max_length=1000)
    disabled: StrictBool | None = None
    includeGuids: StrictBool | None = None
    includeMetadataTypes: StrictBool | None = None

    @model_serializer
    def to_payload(self) -> dict[str, object]:
        return {name: value for name, value in self.__dict__.items() if value is not None}

    @model_validator(mode="after")
    def changes_requested(self):
        if all(getattr(self, name) is None for name in (
                "comment", "disabled", "includeGuids", "includeMetadataTypes")):
            raise ValueError("Specify at least one project property to change")
        return self


class ProjectRowsAppend(ProjectName, ProjectConnection):
    expectedState: str = Field(min_length=1, max_length=524288)
    rows: list[ProjectDefinition] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def consistent_rows(self):
        require_consistent_rows(self.projectName, self.connectionName, self.rows)
        return self


class ProjectRowChange(ProjectName):
    expectedState: str = Field(min_length=1, max_length=524288)
    currentTableName: str = Field(pattern=LATIN_NAME_PATTERN, max_length=100)
    definition: ProjectDefinition

    @model_validator(mode="after")
    def consistent_definition(self):
        if self.definition.projectName != self.projectName:
            raise ValueError("Definition must use the existing project")
        if self.definition.tableName.casefold() != self.currentTableName.casefold():
            raise ValueError("Renaming a target table is not supported")
        return self


class ProjectRowSettingsChange(ProjectName):
    """Patch saved row attributes without rebuilding mappings or receiver DDL."""

    expectedState: str = Field(min_length=1, max_length=524288)
    currentTableName: str = Field(pattern=LATIN_NAME_PATTERN, max_length=100)
    rowSettings: RowSettings


class FieldMappingChange(StrictRequest):
    """Patch a saved mapping, keeping its type, calculation and segment roles."""

    source: str = Field(min_length=1, max_length=512,
                        pattern=FIELD_PATH_PATTERN)
    target: str | None = Field(default=None, pattern=LATIN_NAME_PATTERN, max_length=100)
    noExport: StrictBool | None = None
    add: Literal[True] | None = None

    @model_validator(mode="before")
    @classmethod
    def explicit_changes(cls, value: object) -> object:
        if isinstance(value, dict):
            if "add" in value and value["add"] is not True:
                raise ValueError("add must be Boolean true")
            changes = {key: item for key, item in value.items() if key != "source"}
            if not changes or any(item is None for item in changes.values()):
                raise ValueError("Specify target, noExport or add; omit unchanged properties")
        return value

    @model_validator(mode="after")
    def validate_addition(self) -> "FieldMappingChange":
        if self.add and (self.target is None or self.noExport is not None or "." in self.source):
            raise ValueError("Addition requires target and a direct source field")
        return self

    @model_serializer(mode="wrap")
    def explicit_payload(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        return {key: value for key, value in handler(self).items()
                if key in self.model_fields_set}


class ProjectFieldMappingChange(ProjectName):
    """Patch one saved row without rebuilding its source or mapping."""

    expectedState: str = Field(min_length=1, max_length=524288)
    currentTableName: str | None = Field(default=None, pattern=LATIN_NAME_PATTERN, max_length=100)
    rowIdentifier: int | None = Field(default=None, strict=True, gt=0)
    fieldChanges: list[FieldMappingChange] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_sources(self) -> "ProjectFieldMappingChange":
        if (self.currentTableName is None) == (self.rowIdentifier is None):
            raise ValueError("Specify exactly one row selector")
        if self.rowIdentifier is None and any(change.add for change in self.fieldChanges):
            raise ValueError("Field addition requires rowIdentifier")
        sources = [change.source for change in self.fieldChanges]
        if len(set(sources)) != len(sources):
            raise ValueError("Duplicate mapping sources")
        targets = [change.target.casefold() for change in self.fieldChanges if change.target is not None]
        if len(set(targets)) != len(targets):
            raise ValueError("Duplicate target column names")
        return self

    @model_serializer(mode="wrap")
    def omit_unused_selector(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        payload = handler(self)
        payload.pop("currentTableName" if self.currentTableName is None else "rowIdentifier", None)
        return payload


class ProjectReceiverRepair(ProjectName):
    """Repair one absent MySQL receiver; never accept SQL or native templates."""

    expectedState: str = Field(min_length=1, max_length=524288)
    currentTableName: str = Field(pattern=LATIN_NAME_PATTERN, max_length=100)
    receiverRepair: Literal["create_missing_mysql"]
    textColumns: list[str] = Field(
        min_length=1, max_length=50,
        description="Saved string columns selected for TEXT: ordinary strings, native metadata type names, or confirmed enum presentations/names. Preserve native GUID/UUID types and key/segment fields; EPA checks metadata and calculation semantics, not column spelling.",
    )

    @model_validator(mode="after")
    def valid_text_columns(self) -> "ProjectReceiverRepair":
        if any(not re.fullmatch(BOUNDED_LATIN_NAME, name)
               for name in self.textColumns):
            raise ValueError("TEXT columns must be saved Latin receiver identifiers")
        if len({name.casefold() for name in self.textColumns}) != len(self.textColumns):
            raise ValueError("Duplicate TEXT column")
        return self


# Project-level and row-level changes share the same public preview/update
# operations; the closed union still validates each concrete payload strictly.
ProjectChange = (ProjectUpdate | ProjectRowsAppend | ProjectRowChange | ProjectReceiverRepair
                 | ProjectRowSettingsChange | ProjectFieldMappingChange)
ProjectPreview = (ProjectDefinition | GroupProjectDefinition | ProjectRowsAppend | ProjectRowChange
                  | ProjectReceiverRepair | ProjectRowSettingsChange | ProjectFieldMappingChange)
