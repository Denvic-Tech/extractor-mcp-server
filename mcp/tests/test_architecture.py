"""Направление зависимостей между слоями пакета.

domain не знает ни о ком; application знает только domain; infrastructure
реализует порты и не знает об interface; interface связывает всё вместе.
"""
import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "extractor1c"
LAYERS = ("domain", "application", "infrastructure", "interface")
ALLOWED = {
    "domain": {"domain"},
    "application": {"domain", "application"},
    "infrastructure": {"domain", "application", "infrastructure"},
    "interface": set(LAYERS),
}
# Библиотеки ввода-вывода и протокола, которым нет места в сценариях и домене.
FORBIDDEN_LIBRARIES = {
    "domain": {"requests", "mcp", "fastapi", "starlette", "uvicorn", "sqlite3", "yaml", "pydantic_settings"},
    "application": {"requests", "mcp", "fastapi", "starlette", "uvicorn", "sqlite3", "yaml", "pydantic_settings"},
    "infrastructure": {"mcp", "fastapi", "starlette", "uvicorn"},
    "interface": set(),
}


def modules():
    return sorted(path for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts)


def imported_names(path: Path) -> list[str]:
    """Абсолютные имена модулей, которые импортирует файл."""
    package = ("extractor1c", *path.relative_to(PACKAGE).parts[:-1])
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - node.level + 1]
                names.append(".".join((*base, *(node.module.split(".") if node.module else ()))))
                if not node.module:
                    names += [".".join((*base, alias.name)) for alias in node.names]
            else:
                names.append(node.module)
    return names


@pytest.mark.parametrize("path", modules(), ids=lambda path: path.relative_to(PACKAGE).as_posix())
def test_module_respects_layer_boundaries(path):
    parts = path.relative_to(PACKAGE).parts
    if parts[0] not in LAYERS:
        return  # корневые фасады пакета могут импортировать любой слой
    layer = parts[0]
    for name in imported_names(path):
        pieces = name.split(".")
        if pieces[0] == "extractor1c" and len(pieces) > 1 and pieces[1] in LAYERS:
            assert pieces[1] in ALLOWED[layer], f"{layer} не может зависеть от {pieces[1]}: {name}"
        assert pieces[0] not in FORBIDDEN_LIBRARIES[layer], f"{layer} не может импортировать {pieces[0]}"


def test_infrastructure_uses_application_only_through_ports():
    for path in modules():
        parts = path.relative_to(PACKAGE).parts
        if parts[0] != "infrastructure":
            continue
        for name in imported_names(path):
            if name.startswith("extractor1c.application"):
                assert name == "extractor1c.application.ports", name


def test_every_package_directory_is_importable():
    directories = {path.parent for path in modules()}
    assert all((directory / "__init__.py").is_file() for directory in directories)
