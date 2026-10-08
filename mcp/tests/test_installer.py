"""Installer regression: protect credentials and existing customer configuration."""
import json
from pathlib import Path
import runpy

import pytest
import yaml
from dotenv import dotenv_values

INSTALLER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "install.py"))


def test_direct_credentials_roundtrip(tmp_path):
    connection = {"base_id": "erp", "name": "ERP", "url": "https://host/base/hs/extractor-projects/v1",
                  "user": "Администратор", "password": "p'\\#:$special\""}
    env, bases = INSTALLER["make_configuration"]("direct_http", "local", connection)
    INSTALLER["save_configuration"](tmp_path, env, bases)
    settings = dotenv_values(tmp_path / ".env")
    assert settings["EXTRACTOR_PROJECTS_TRANSPORT"] == "direct_http"
    assert settings["EXTRACTOR_PROJECTS_BASES_FILE"] == "bases.yaml"
    assert len(settings["EXTRACTOR_PROJECTS_MCP_TOKENS"]) >= 32
    assert yaml.safe_load((tmp_path / "bases.yaml").read_text(encoding="utf-8"))["bases"] == [connection]


def test_dcc_secret_roundtrip_and_no_direct_base(tmp_path):
    connection = {"url": "https://dcc.example/api", "user": "mcp", "password": "x'\\#:$\""}
    env, bases = INSTALLER["make_configuration"]("dcc", "docker", connection)
    INSTALLER["save_configuration"](tmp_path, env, bases)
    settings = dotenv_values(tmp_path / ".env")
    assert settings["EXTRACTOR_PROJECTS_DCC_PASSWORD"] == connection["password"]
    assert "EXTRACTOR_PROJECTS_BASES_FILE" not in settings
    assert settings["EXTRACTOR_PROJECTS_OPERATION_DB"] == "/var/lib/extractor1c/operations.sqlite3"
    assert json.loads(bases) == {"bases": []}


def test_refuse_existing_without_touching_either_file(tmp_path):
    original = tmp_path / "bases.yaml"
    original.write_text("private-original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        INSTALLER["save_configuration"](tmp_path, "new", "new")
    assert original.read_text(encoding="utf-8") == "private-original"
    assert not (tmp_path / ".env").exists()


def test_explicit_overwrite_backs_up_originals(tmp_path):
    directory = tmp_path / "mcp"
    directory.mkdir()
    for name in (".env", "bases.yaml"):
        (directory / name).write_text("original-" + name, encoding="utf-8")
    INSTALLER["save_configuration"](directory, "new-env", "new-bases", overwrite=True)
    backups = list((tmp_path / ".local-backups").glob("installer-*"))
    assert len(backups) == 1
    assert (backups[0] / ".env").read_text(encoding="utf-8") == "original-.env"
    assert (backups[0] / "bases.yaml").read_text(encoding="utf-8") == "original-bases.yaml"


@pytest.mark.parametrize("value", ["https://u:p@host/base/hs/extractor-projects/v1", "https://host/docs", "ftp://host/base", "https://host/base/hs/extractor-projects/v1?token=secret"])
def test_reject_misleading_or_credential_urls(value):
    with pytest.raises(ValueError):
        INSTALLER["endpoint"](value, direct=True)


@pytest.mark.parametrize("value", ["x\nPASSWORD=leaked", "${EXPAND_ME}"])
def test_reject_dotenv_injection(value):
    with pytest.raises(ValueError):
        INSTALLER["dotenv_value"](value)
