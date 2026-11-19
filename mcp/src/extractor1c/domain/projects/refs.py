"""Ссылка на сохранённый проект по его GUID."""
from pydantic import Field

from ..requests import StrictRequest
from ..identifiers import UUID_PATTERN


class ProjectScheduleRef(StrictRequest):
    projectGuid: str = Field(pattern=UUID_PATTERN)
