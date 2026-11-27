"""Правила маскирования по исходным именам полей 1С."""
import re
from typing import Any

_IDENTIFIER = re.compile(r"\w+(?:\s*\.\s*\w+)*")
_SEPARATORS = re.compile(r"[\s_-]+")
_PERSONAL_FIELD = re.compile(
    r"фио|фамилия|имя|отчество|фамилияимяотчество|"
    r"датарождения|месторождения|местожительства|гражданство|"
    r"адрес(?!сервера|подключения|сайта|ресурса)\w*|"
    r"(?:почтовый|фактический|юридический|домашний)адрес|"
    r"\w*телефон\w*|email\w*|электроннаяпочта\w*|почта|"
    r"представление(?:адреса|телефона|контактнойинформации)|контактнаяинформация\w*|"
    r"инн\w*|снилс\w*|паспорт\w*|полисомс\w*|номерполисаомс|"
    r"страховойномерпфр|водительскоеудостоверение\w*|"
    r"документудостоверяющийличность\w*|"
    r"(?:серия|номер|датавыдачи|кемвыдан|кодподразделения)паспорта|"
    r"номер(?:банковского|лицевого)счета|номербанковскойкарты"
)
_PERSON_OBJECTS = {"физическиелица", "сотрудники", "сотрудникиорганизаций"}
_CONTACT_PART = re.compile(r"(?:партн[её]ры|контрагенты|организации)?контактнаяинформация|контакты")
_CONTACT_VALUE = re.compile(r"представление|значение|значенияполей|доменноеимясервера")


def _mask_field(source: object, source_object: object) -> bool:
    if not isinstance(source, str) or not _IDENTIFIER.fullmatch(source.strip()):
        return True
    path = [_SEPARATORS.sub("", part.casefold()) for part in source.split(".")]
    if any(_PERSONAL_FIELD.fullmatch(part) for part in path):
        return True
    objects = (source_object.casefold().split(".") if isinstance(source_object, str) else [])
    if _CONTACT_VALUE.fullmatch(path[-1]) and any(
        _CONTACT_PART.fullmatch(part) for part in [*objects, *path[:-1]]
    ):
        return True
    return path[-1] in {"наименование", "представление", "ссылка"} and bool(
        _PERSON_OBJECTS.intersection([*objects, *path[:-1]])
    )


def mask_receiver_sample(value: Any) -> Any:
    """Маскировать значения в копии выборки, сохраняя исходный ответ EPA."""
    if not isinstance(value, dict) or not isinstance(value.get("checks"), dict):
        return value
    checks = value["checks"]
    sample = checks.get("sample")
    if not isinstance(sample, dict) or not isinstance(sample.get("result"), dict):
        return value
    result = sample["result"]
    rows = result.get("rows")
    if not isinstance(rows, list):
        return value
    fields = result.get("fields")
    safe_names = set()
    if isinstance(fields, list):
        unsafe_names = set()
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("name"), str):
                continue
            names = unsafe_names if _mask_field(field.get("source"), value.get("source")) else safe_names
            names.add(field["name"])
        safe_names -= unsafe_names
    masked_names = set()
    masked_rows = []
    for row in rows:
        if not isinstance(row, dict):
            masked_rows.append(None)
            continue
        masked_names.update(row.keys() - safe_names)
        masked_rows.append({key: item if key in safe_names else "[СКРЫТО]" for key, item in row.items()})
    if not masked_names and masked_rows == rows:
        return value
    masked_result = {**result, "rows": masked_rows, "maskedFields": sorted(masked_names)}
    return {**value, "checks": {**checks, "sample": {**sample, "result": masked_result}}}
