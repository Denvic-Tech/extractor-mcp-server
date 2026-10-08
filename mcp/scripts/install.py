"""Interactive single-base/DCC installer; bootstrap uses Python's standard library.

Run from the source checkout with Python >= 3.11. Configuration only never
contacts 1C/DCC. Existing configuration is preserved unless --overwrite is given.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from getpass import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
import venv


def ask(prompt: str, default: str = "") -> str:
    """Read a non-secret answer, substituting default for empty input."""
    return input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip() or default


def yes(prompt: str, default: bool = False) -> bool:
    """Read a yes/no answer; blank input preserves the displayed default."""
    while True:
        value = ask(prompt + " (да/нет)", "да" if default else "нет").lower()
        if value in {"да", "д", "yes", "y"}:
            return True
        if value in {"нет", "н", "no", "n"}:
            return False
        print("Введите да или нет.")


def endpoint(value: str, *, direct: bool) -> str:
    """Validate a credential-free base/DCC URL; direct URLs require the API suffix."""
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(char.isspace() for char in value)):
        raise ValueError("Нужен полный http(s) URL без учётных данных, параметров и пробелов.")
    value = value.rstrip("/")
    if direct and not parsed.path.rstrip("/").endswith("/hs/extractor-projects/v1"):
        raise ValueError("Адрес 1С должен оканчиваться на /hs/extractor-projects/v1.")
    if not direct and parsed.path.rstrip("/") not in {"", "/api"}:
        raise ValueError("Укажите корень DCC или /api, без /docs и /v2.")
    return value


def dotenv_value(value: str) -> str:
    """Quote literal one-line dotenv values without logging them.

    ${...} is rejected because dotenv loaders may interpolate it differently.
    Such credentials can instead be passed through the process environment.
    """
    if any(char in value for char in "\r\n\0") or "${" in value:
        raise ValueError("Значение .env содержит перенос строки или ${...}; передайте его через окружение процесса.")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def make_configuration(mode: str, deployment: str, connection: dict) -> tuple[str, str]:
    """Return secret dotenv/YAML documents for one direct base or all DCC bases.

    JSON is emitted as valid YAML to preserve special characters in credentials
    without a PyYAML bootstrap dependency. The Docker DCC mount uses an empty list.
    """
    settings = {
        "EXTRACTOR_PROJECTS_TRANSPORT": mode,
        "EXTRACTOR_PROJECTS_MCP_TOKENS": secrets.token_urlsafe(32),
        "EXTRACTOR_PROJECTS_TIMEOUT": "60",
        "EXTRACTOR_PROJECTS_PLAN_TTL_SECONDS": "900",
        "EXTRACTOR_PROJECTS_OPERATION_DB": "state/operations.sqlite3",
    }
    bases = []
    if mode == "direct_http":
        settings["EXTRACTOR_PROJECTS_BASES_FILE"] = "bases.yaml"
        settings["EXTRACTOR_PROJECTS_DEFAULT_BASE_ID"] = connection["base_id"]
        bases = [connection]
    else:
        settings.update({
            "EXTRACTOR_PROJECTS_DCC_URL": connection["url"],
            "EXTRACTOR_PROJECTS_DCC_USER": connection["user"],
            "EXTRACTOR_PROJECTS_DCC_PASSWORD": connection["password"],
            "EXTRACTOR_PROJECTS_DCC_WAIT_TIMEOUT": "60",
            "EXTRACTOR_PROJECTS_DCC_POLL_INTERVAL": "1",
        })
        if connection.get("allow_http_dev"):
            settings["EXTRACTOR_PROJECTS_ALLOW_HTTP_DEV"] = "true"
    if deployment == "docker":
        settings["EXTRACTOR_PROJECTS_OPERATION_DB"] = "/var/lib/extractor1c/operations.sqlite3"
    env_text = "# Создан установщиком MCP. Секреты: не публиковать и не добавлять в Git.\n"
    env_text += "\n".join(f"{key}={dotenv_value(value)}" for key, value in settings.items()) + "\n"
    return env_text, json.dumps({"bases": bases}, ensure_ascii=False, indent=2) + "\n"


def save_configuration(directory: Path, env_text: str, bases_text: str, *, overwrite: bool = False) -> None:
    """Save private configuration; refuse replacement or back up explicitly replaced files."""
    documents = {directory / ".env": env_text, directory / "bases.yaml": bases_text}
    existing = [path for path in documents if path.exists()]
    if existing and not overwrite:
        raise FileExistsError("Конфигурация уже существует. Она сохранена; для замены используйте --overwrite.")
    if existing:
        backup = directory.parent / ".local-backups" / ("installer-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        backup.mkdir(parents=True, mode=0o700)
        for path in existing:
            shutil.copy2(path, backup / path.name)
    for path, document in documents.items():
        # On Unix create with private permissions before writing any credentials.
        flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if overwrite else os.O_EXCL)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
        if os.name != "nt":
            path.chmod(0o600)


def main() -> int:
    """Prompt for deployment and route, save settings and optionally install/start MCP.

    Exit 0 on success; failure prints no credentials. --configure-only skips
    dependency installation and startup. --overwrite preserves a private backup.
    """
    parser = argparse.ArgumentParser(description="Пошаговая установка MCP Экстрактора 1С")
    parser.add_argument("--configure-only", action="store_true", help="Только создать настройки")
    parser.add_argument("--overwrite", action="store_true", help="Заменить настройки с резервной копией")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Нужен Python 3.11 или новее")
    directory = Path(__file__).resolve().parents[1]
    if not args.overwrite and any((directory / name).exists() for name in (".env", "bases.yaml")):
        print(".env или bases.yaml уже существует. Ничего не изменено. См. --overwrite.")
        return 1
    try:
        print("Установка MCP 3.2.0. Пароли вводятся скрыто. Базу 1С установщик не изменяет.")
        deployment = "docker" if yes("Запускать в Docker?") else "local"
        mode = "dcc" if yes("Использовать DCC v2?") else "direct_http"
        connection = {}
        if mode == "direct_http":
            print("Нужна web-публикация 1С, HTTP-сервис extractor-projects и публикация HTTP-сервисов расширений.")
            connection["base_id"] = ask("Идентификатор базы", "erp")
            connection["name"] = ask("Название базы", "ERP")
        connection["url"] = endpoint(ask("URL DCC" if mode == "dcc" else "URL API 1С (/hs/extractor-projects/v1)"), direct=mode == "direct_http")
        if connection["url"].startswith("http://"):
            if not yes("HTTP передаёт учётные данные без TLS. Это доверенная тестовая сеть?"):
                raise ValueError("Укажите адрес HTTPS.")
            connection["allow_http_dev"] = True
        connection["user"] = ask("Пользователь DCC" if mode == "dcc" else "Пользователь 1С")
        if not connection["user"]:
            raise ValueError("Пользователь обязателен.")
        connection["password"] = getpass("Пароль (для 1С допустим пустой): ")
        if mode == "dcc" and not connection["password"]:
            raise ValueError("Для DCC требуется непустой пароль.")
        env_text, bases_text = make_configuration(mode, deployment, connection)
        save_configuration(directory, env_text, bases_text, overwrite=args.overwrite)
        print("Созданы закрытые .env и bases.yaml. MCP-токен находится в .env; он не выводится.")
        print("На Windows ограничьте права файлов своей учётной записью/учётной записью службы.")
        if args.configure_only:
            return 0
        if deployment == "docker":
            if yes("Собрать и запустить контейнер сейчас?", True):
                subprocess.run(["docker", "compose", "-f", "docker-compose.install.yml", "up", "-d", "--build"], cwd=directory, check=True)
            print("Проверка: http://127.0.0.1:8001/health; MCP: http://127.0.0.1:8001/mcp")
        else:
            environment = directory / ".venv"
            if yes("Создать .venv и установить зависимости сейчас?", True):
                venv.EnvBuilder(with_pip=True).create(environment)
                python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                subprocess.run([str(python), "-m", "pip", "install", "-e", "."], cwd=directory, check=True)
            print("Запуск из mcp/: .venv/Scripts/python.exe -m extractor1c.mcp_server (Windows)")
            print("или .venv/bin/python -m extractor1c.mcp_server (Unix). Для ИИ-клиента добавьте --transport stdio.")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError):
        print("Установка не завершена: проверьте введённый URL, компоненты и права. Секреты не выводятся.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
