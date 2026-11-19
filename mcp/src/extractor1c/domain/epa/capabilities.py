"""Возможности опубликованных методов 1С (EPA), о которых сообщает service/readiness.

Чтение возможностей — оптимизация и ранний отказ, а не право на запись:
EPA сама проверяет приёмник непосредственно перед побочным действием.
"""
from dataclasses import dataclass
from typing import Any

from ..identifiers import is_version

# Текст отказа для каждой возможности, без которой сценарий не начинается.
CAPABILITY_REQUIRED = {
    "direct_sql_operations": "Direct SQL operations require the direct_sql_operations EPA capability; update EPA",
    "field_mapping": "Field mapping changes require the field_mapping EPA capability; update EPA",
    "field_selection": "Row field selection requires the field_selection EPA capability; update EPA",
    "column_names": "Column names require the column_names EPA capability; update EPA",
    "query_sources": "Query sources require the query_sources EPA capability; update EPA",
    "row_settings": "Native row settings require the row_settings EPA capability; update EPA",
    "mysql_receiver_repair": "MySQL receiver repair requires the mysql_receiver_repair EPA capability; update EPA",
    "query_diagnostics": "Query diagnostics require the query_diagnostics EPA capability; update EPA",
    "receiver_inspection": "Receiver inspection is not advertised by the selected EPA publication; update EPA",
}


@dataclass(frozen=True)
class PublishedEpa:
    """Ответ service/readiness: версия методов и объявленные возможности."""

    methods_version: Any
    capabilities: tuple[Any, ...] | None

    @classmethod
    def from_readiness(cls, payload: Any) -> "PublishedEpa":
        if not isinstance(payload, dict):
            return cls(None, None)
        capabilities = payload.get("capabilities")
        return cls(payload.get("methodsVersion"),
                   tuple(capabilities) if isinstance(capabilities, list) else None)

    def supports(self, name: str) -> bool:
        return self.capabilities is not None and name in self.capabilities

    def first_missing(self, *names: str) -> str | None:
        return next((name for name in names if not self.supports(name)), None)

    def version_tuple(self) -> tuple[int, int, int] | None:
        """Версия методов как числа; ``None``, если EPA не сообщила её в виде ``X.Y.Z``."""
        if not is_version(self.methods_version):
            return None
        major, minor, patch = map(int, self.methods_version.split("."))
        return major, minor, patch
