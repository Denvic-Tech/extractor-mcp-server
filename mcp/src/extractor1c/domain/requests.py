"""Закрытые схемы запросов: лишние поля и неявные приведения типов запрещены."""
from pydantic import BaseModel, ConfigDict


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyRequest(StrictRequest):
    pass
