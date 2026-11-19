"""Формы имён 1С, колонок приёмника и идентификаторов, общие для закрытых схем.

Шаблоны с суффиксом ``_PATTERN`` заякорены и попадают в JSON Schema инструментов,
поэтому их текст является частью публичного контракта. Остальные используются
в ``re.fullmatch``.
"""
import re

NAME = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*"
BOUNDED_NAME = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]{0,99}"
FIELD_PATH = NAME + r"(?:\." + NAME + r")*"
LATIN_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
BOUNDED_LATIN_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,99}"
UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
VERSION = r"\d+\.\d+\.\d+"

NAME_PATTERN = "^" + NAME + "$"
QUALIFIED_NAME_PATTERN = "^" + NAME + r"\." + NAME + "$"
OBJECT_NAME_PATTERN = "^" + NAME + r"(?:\." + NAME + r"){1,2}$"
OBJECT_PATH_PATTERN = "^" + NAME + r"(?:\." + NAME + r")+$"
FIELD_PATH_PATTERN = "^" + FIELD_PATH + "$"
TABLE_NAME_PATTERN = r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_.]*$"
LATIN_NAME_PATTERN = "^" + LATIN_NAME + "$"
UUID_PATTERN = "^" + UUID + "$"


def is_uuid(value: object) -> bool:
    """Точный текстовый UUID в канонической записи с дефисами."""
    return isinstance(value, str) and re.fullmatch(UUID, value) is not None


def is_version(value: object) -> bool:
    """Версия опубликованных методов вида ``3.3.5``."""
    return isinstance(value, str) and re.fullmatch(VERSION, value) is not None


def has_control_characters(text: str) -> bool:
    return any(ord(char) < 32 for char in text)
