"""Определение проекта выгрузки: источник, приёмник, сегментация и настройки строки."""
from datetime import datetime
import math
import re
from typing import Literal

from pydantic import (
    Field, SerializerFunctionWrapHandler, StrictBool, StrictInt, model_serializer, model_validator,
)

from ..identifiers import (
    BOUNDED_LATIN_NAME, BOUNDED_NAME, FIELD_PATH, FIELD_PATH_PATTERN, NAME, NAME_PATTERN, OBJECT_NAME_PATTERN,
    QUALIFIED_NAME_PATTERN, TABLE_NAME_PATTERN, UUID, has_control_characters,
)
from ..metadata.requests import ObjectRequest
from ..requests import StrictRequest


class ProjectName(StrictRequest):
    projectName: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_project_name(self):
        if not self.projectName.strip() or has_control_characters(self.projectName):
            raise ValueError("Project name must be nonempty and contain no control characters")
        return self


class ProjectConnection(StrictRequest):
    # Omitted names are resolved before an MCP preview is stored as a plan.
    connectionName: str = Field(default="", max_length=150)

    @model_validator(mode="after")
    def valid_connection_name(self):
        if (self.connectionName and not self.connectionName.strip()) or has_control_characters(
            self.connectionName
        ):
            raise ValueError("Invalid connection name")
        return self


# Опциональная трёхролевая сегментация, закрытые схемы и совместимость старых запросов.
class Segmentation(StrictRequest):
    """One native grouping with independently selected execution roles (API 3.2.2)."""
    segment: Literal["По значению", "Первая буква", "Первые две буквы", "Произвольная подстрока",
                     "Час", "День", "Неделя", "Месяц", "Квартал", "Полугодие", "Год"]
    field: str = Field(min_length=1, max_length=512,
                       pattern=FIELD_PATH_PATTERN)
    regular: StrictBool
    extended: StrictBool
    initialization: StrictBool

    @model_validator(mode="after")
    def valid_roles(self):
        if not (self.regular or self.extended or self.initialization):
            raise ValueError("At least one segmentation role must be selected")
        return self


class RowSettings(StrictRequest):
    """Explicit native row attributes; omitted values preserve native settings."""

    maxQueueRecords: StrictInt | None = Field(default=None, ge=0, le=9999999999)
    queueBatchSize: StrictInt | None = Field(default=None, ge=0, le=9999999999)
    maxThreads: StrictInt | None = Field(default=None, ge=0, le=99)
    threadWaitTimeout: StrictInt | None = Field(default=None, ge=0, le=9999999999)
    bufferSize: StrictInt | None = Field(default=None, ge=-9999999999, le=9999999999)
    blockSize: StrictInt | None = Field(default=None, ge=0, le=9999999999)
    deleteInBlocks: StrictBool | None = None
    deleteBlockSize: StrictInt | None = Field(default=None, ge=0, le=9999999999)
    advancedDataCheck: StrictBool | None = None
    parameterExpansionThreshold: StrictInt | None = Field(default=None, ge=0, le=9999999999)

    @model_validator(mode="before")
    @classmethod
    def explicit_nonnull_settings(cls, value: object) -> object:
        if isinstance(value, dict) and (not value or any(v is None for v in value.values())):
            raise ValueError("Specify non-null native row settings; omit unchanged properties")
        return value

    @model_serializer(mode="wrap")
    def explicit_payload(self, handler: SerializerFunctionWrapHandler) -> dict[str, int | bool]:
        return {key: value for key, value in handler(self).items()
                if key in self.model_fields_set}


class QueryParameter(StrictRequest):
    """A fixed typed 1C query value; segment values come from the native queue."""

    name: str = Field(pattern=NAME_PATTERN, max_length=100)
    type: Literal["string", "number", "boolean", "date", "reference", "enum"]
    value: str | StrictInt | float | StrictBool | list[str | StrictInt | float | StrictBool]
    object: str | None = Field(default=None, max_length=512,
                              pattern=QUALIFIED_NAME_PATTERN)

    @model_validator(mode="after")
    def valid_typed_value(self):

        if self.type in {"reference", "enum"}:
            if not self.object:
                raise ValueError("Reference and enum parameters require their metadata object")
            if self.type == "enum" and not self.object.startswith("Перечисление."):
                raise ValueError("Enum parameter requires Перечисление.<Имя>")
            if self.type == "reference" and self.object.split(".")[0] not in {
                "Справочник", "Документ", "ПланСчетов", "ПланВидовХарактеристик",
                "ПланВидовРасчета", "БизнесПроцесс", "Задача",
            }:
                raise ValueError("Reference parameter requires reference metadata")
        elif "object" in self.model_fields_set:
            raise ValueError("Only reference and enum parameters accept object")
        values = self.value if isinstance(self.value, list) else [self.value]
        if not values or len(values) > 500:
            raise ValueError("Fixed parameter lists require 1..500 values")
        for value in values:
            if self.type == "boolean":
                valid = type(value) is bool
            elif self.type == "number":
                valid = type(value) in {int, float} and math.isfinite(value)
            else:
                valid = isinstance(value, str) and len(value) <= 4096
                if valid and self.type == "date":
                    valid = re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", value) is not None
                    if valid:
                        try:
                            datetime.fromisoformat(value)
                        except ValueError:
                            valid = False
                elif valid and self.type == "reference":
                    valid = re.fullmatch(UUID, value) is not None
                elif valid and self.type == "enum":
                    valid = re.fullmatch(NAME, value) is not None
            if not valid:
                raise ValueError("Fixed parameter value does not match its declared type")
        return self


class ProjectDefinition(ObjectRequest, ProjectName, ProjectConnection):
    # У полного Query нет обязательного объекта привязки. Для объектного источника
    # и Query с сегментацией непустой object проверяется ниже.
    object: str = Field(default="", pattern=OBJECT_NAME_PATTERN, max_length=512)
    tableName: str = Field(pattern=TABLE_NAME_PATTERN, max_length=100)
    segment: Literal["", "День", "Месяц", "Первые две буквы", "По значению"] = ""
    segmentation: list[Segmentation] | None = Field(default=None, max_length=500)
    periodicity: Literal["", "Час", "День", "Неделя", "Месяц", "Квартал", "Полугодие", "Период",
                         "Запись", "Регистратор", "Секунда", "Минута", "Декада", "Авто"] | None = None
    segmentField: str = Field(default="", max_length=512)
    guidColumnNames: dict[str, str] = Field(default_factory=dict)
    # Ключ — точное имя поля сопоставления; меняется только SQL-имя приёмника.
    columnNames: dict[str, str] = Field(default_factory=dict)
    includeGuids: StrictBool = True
    includeMetadataTypes: StrictBool = True
    maxItemsPerBatch: StrictInt = Field(default=0, ge=0, le=1000000)
    selectedFields: list[str] = Field(default_factory=list, max_length=500)
    rowSettings: RowSettings | None = None
    queryText: str | None = Field(default=None, min_length=1, max_length=200000)
    queryParameterNames: dict[str, str] | None = Field(default=None, max_length=500)
    queryParameters: list[QueryParameter] | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def valid_query_source(self):
        query_keys = {"queryText", "queryParameterNames", "queryParameters"}
        specified = query_keys & self.model_fields_set
        if not specified:
            if not self.object:
                raise ValueError("Object source requires object")
            return self
        if self.queryText is None or not self.queryText.strip() or "\x00" in self.queryText:
            raise ValueError("Query source requires nonblank queryText")
        if any(getattr(self, key) is None for key in specified):
            raise ValueError("Omit unused query source properties instead of null")
        if self.segmentation is None:
            raise ValueError("Query source requires an explicit segmentation array")
        if self.segmentation and not self.object:
            raise ValueError("Segmented Query requires the selected parameter-controlled object")
        identifier = BOUNDED_NAME
        names = self.queryParameterNames or {}
        if any(not re.fullmatch(identifier, key) or not re.fullmatch(identifier, value)
               for key, value in names.items()):
            raise ValueError("Invalid query segment parameter name")
        aliases = [name.casefold() for name in names.values()]
        if len(set(aliases)) != len(aliases):
            raise ValueError("Duplicate query segment parameter names")
        if names and not self.segmentation:
            raise ValueError("Parameter bindings require segmentation")
        fixed = [parameter.name.casefold() for parameter in self.queryParameters or []]
        if len(set(fixed)) != len(fixed) or set(fixed) & set(aliases):
            raise ValueError("Duplicate or overlapping fixed and segment query parameters")
        return self

    @model_validator(mode="after")
    def segment_matches(self):
        if "rowSettings" in self.model_fields_set and self.rowSettings is None:
            raise ValueError("rowSettings must be omitted or a nonempty object")
        if (self.rowSettings is not None and self.rowSettings.queueBatchSize is not None
                and "maxItemsPerBatch" in self.model_fields_set
                and self.maxItemsPerBatch != self.rowSettings.queueBatchSize):
            raise ValueError("maxItemsPerBatch and rowSettings.queueBatchSize refer to the same native property")
        # Один источник выбора: новый контракт не смешивается с прежними параметрами.
        if self.segmentation is not None:
            if self.segment or self.segmentField:
                raise ValueError("Do not combine segmentation with segment/segmentField")
            return self.valid_mapping_fields()
        self.valid_mapping_fields()
        field_path = FIELD_PATH
        if self.segment in {"Первые две буквы", "По значению"}:
            if not re.fullmatch(field_path, self.segmentField):
                raise ValueError("Field segmentation requires a field path")
            return self
        if self.segmentField:
            raise ValueError("segmentField requires text or value segmentation")
        if self.object.startswith("Справочник.") != (self.segment == ""):
            raise ValueError("Catalog: empty segment; register history: День or Месяц")
        return self

    @model_serializer(mode="wrap")
    def api_payload(self, handler):
        """Do not emit mutually exclusive simple fields alongside a grouping array."""
        payload = handler(self)
        if self.queryText is not None and not self.object:
            payload.pop("object", None)
        for key in ("queryText", "queryParameterNames", "queryParameters"):
            if getattr(self, key) is None:
                payload.pop(key, None)
        # Старые EPA используют закрытую схему: новый пустой ключ им не отправляем.
        if not self.columnNames:
            payload.pop("columnNames", None)
        if self.segmentation is not None:
            payload.pop("segment", None)
            payload.pop("segmentField", None)
        # EPA трактует переданную пустую строку как неизвестную периодичность.
        if not self.periodicity:
            payload.pop("periodicity", None)
        if self.rowSettings is None:
            payload.pop("rowSettings", None)
        elif (self.rowSettings.queueBatchSize is not None
              and "maxItemsPerBatch" not in self.model_fields_set):
            payload.pop("maxItemsPerBatch", None)
        return payload

    def valid_mapping_fields(self):
        identifier = BOUNDED_LATIN_NAME
        field_path = FIELD_PATH
        if any(len(name) > 512 or not re.fullmatch(field_path, name) for name in self.selectedFields):
            raise ValueError("Invalid selected field")
        if len(set(self.selectedFields)) != len(self.selectedFields):
            raise ValueError("Duplicate selected fields")
        if len(self.guidColumnNames) > 200 or any(
            not re.fullmatch(field_path, source) or not re.fullmatch(identifier, target)
            for source, target in self.guidColumnNames.items()
        ):
            raise ValueError("Invalid GUID column mapping")
        if len(set(self.guidColumnNames.values())) != len(self.guidColumnNames):
            raise ValueError("Duplicate GUID column names")
        if len(self.columnNames) > 500 or any(
            len(source) > 512 or not re.fullmatch(field_path, source)
            or not re.fullmatch(identifier, target)
            for source, target in self.columnNames.items()
        ):
            raise ValueError("Invalid column mapping")
        if self.columnNames.keys() & self.guidColumnNames.keys():
            raise ValueError("Do not map the same source in columnNames and guidColumnNames")
        # Оба словаря задают один набор колонок, поэтому коллизии проверяем вместе.
        names = [name.casefold() for name in (*self.columnNames.values(), *self.guidColumnNames.values())]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate column names")
        return self


def require_consistent_rows(project_name: str, connection_name: str, rows: list[ProjectDefinition]) -> None:
    """Строки одного запроса принадлежат одному проекту, подключению и разным таблицам."""
    names = {name for name in [connection_name, *(row.connectionName for row in rows)] if name}
    if len(names) > 1 or any(row.projectName != project_name for row in rows):
        raise ValueError("Rows must share project and connection")
    tables = [row.tableName.casefold() for row in rows]
    if len(set(tables)) != len(tables):
        raise ValueError("Duplicate target tables")


class GroupProjectDefinition(ProjectName, ProjectConnection):
    rows: list[ProjectDefinition] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def consistent_rows(self):
        require_consistent_rows(self.projectName, self.connectionName, self.rows)
        return self
