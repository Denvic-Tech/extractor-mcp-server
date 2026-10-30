"""Versioned JSON input for native project construction; no handwritten BSL/SQL."""
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .projects_api import ProjectDefinition, ProjectConnection, ProjectName, Segmentation, StrictRequest


class TemplateDefinition(ProjectName, ProjectConnection):
    """Native metadata source, including tabular sections and virtual tables."""

    object: str = Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*){1,2}$", max_length=512)
    tableName: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=100)
    segmentation: list[Segmentation] = Field(max_length=500)
    periodicity: Literal["", "Час", "День", "Неделя", "Месяц", "Квартал", "Полугодие", "Период",
                         "Запись", "Регистратор", "Секунда", "Минута", "Декада", "Авто"] | None = None
    selectedFields: list[str] = Field(default_factory=list, max_length=500)
    includeGuids: StrictBool = True
    includeMetadataTypes: StrictBool = True
    guidColumnNames: dict[str, str] = Field(default_factory=dict)
    maxItemsPerBatch: StrictInt = Field(default=0, ge=0, le=1000000)

    @model_validator(mode="after")
    def validate_native_definition(self):
        """Reuse native field/mapping validation and require unique grouping paths."""
        ProjectDefinition.model_validate(self.model_dump())
        paths = [s.field.casefold() for s in self.segmentation]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate segmentation field")
        if any(s.segment == "По значению" and s.field == "Ссылка" for s in self.segmentation):
            if self.maxItemsPerBatch != 1000:
                raise ValueError("Reference segmentation requires maxItemsPerBatch=1000")
        return self


class ProjectTemplateRequest(StrictRequest):
    """JSON request, distinct from Extractor serialized Type/Value project data."""

    schemaVersion: Literal[1]
    definition: TemplateDefinition

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value):
        if isinstance(value, dict) and type(value.get("schemaVersion")) is not int:
            raise ValueError("schemaVersion must be integer 1")
        return value

    def native_definition(self) -> ProjectDefinition:
        """Return an input for the existing preview/plan flow, without version envelope."""
        return ProjectDefinition.model_validate(self.definition.model_dump())


def project_template_schema() -> dict:
    """Publish validation-mode JSON Schema; semantic checks remain explicit."""
    schema = ProjectTemplateRequest.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:denvic:extractor1c:project-template-request:1"
    # Pydantic validators are not automatically represented by JSON Schema.
    grouping = schema["$defs"]["Segmentation"]
    grouping["anyOf"] = [
        {"properties": {role: {"const": True}}, "required": [role]}
        for role in ("regular", "extended", "initialization")
    ]
    schema["$defs"]["TemplateDefinition"]["allOf"] = [{
        "if": {"properties": {"segmentation": {"contains": {
            "properties": {"field": {"const": "Ссылка"}, "segment": {"const": "По значению"}},
            "required": ["field", "segment"],
        }}}, "required": ["segmentation"]},
        "then": {"properties": {"maxItemsPerBatch": {"const": 1000}}, "required": ["maxItemsPerBatch"]},
    }]
    schema["$defs"]["TemplateDefinition"]["properties"]["selectedFields"]["uniqueItems"] = True
    return schema
