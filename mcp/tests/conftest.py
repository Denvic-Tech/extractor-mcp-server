"""Keep offline tests independent of operator credentials and bases.yaml."""
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_operator_configuration(monkeypatch, tmp_path):
    """Use no local .env; individual tests provide their own explicit settings."""
    from extractor1c import mcp_server

    monkeypatch.setattr(mcp_server, "ENV_FILE", tmp_path / "absent.env")
    for name in list(os.environ):
        if name.startswith("EXTRACTOR_PROJECTS_"):
            monkeypatch.delenv(name)
