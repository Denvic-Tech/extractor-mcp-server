"""Настройки установки: переменные EXTRACTOR_PROJECTS_* и файл mcp/.env."""
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Файл настроек привязан к каталогу установки mcp/, а не к рабочему каталогу
# процесса или ИИ-клиента.
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class ApiSettings(BaseSettings):
    """Separate credentials and URL prevent an accidental downgrade to execute."""
    model_config = SettingsConfigDict(env_prefix="EXTRACTOR_PROJECTS_", env_file=".env", extra="ignore")
    url: str = ""
    user: str = ""
    password: str = ""
    transport: Literal["direct_http", "dcc"] = "direct_http"
    bases: list[dict] = Field(default_factory=list)
    bases_file: str = ""
    default_base_id: str = ""
    dcc_url: str = ""
    dcc_user: str = ""
    dcc_password: str = ""
    dcc_wait_timeout: float = Field(default=60, ge=0, le=3600)
    dcc_poll_interval: float = Field(default=1, gt=0, le=60)
    proxy_key: str = ""
    # Bearer tokens accepted at the MCP endpoint. One or more tokens separated
    # by commas; an optional "label:token" keeps a readable name per client,
    # e.g. "extractor:s3cret-token,ci:another-token". Empty means unconfigured:
    # the endpoint refuses all requests (fail-closed).
    mcp_tokens: str = ""
    timeout: float = 60
    allow_http_dev: bool = False
    ca_bundle: str = ""
    client_cert: str = ""
    client_key: str = ""
    operation_db: str = "state/operations.sqlite3"
    plan_ttl_seconds: int = Field(default=900, ge=60, le=86400)

    @model_validator(mode="after")
    def valid_tls_settings(self):
        if bool(self.client_cert) != bool(self.client_key):
            raise ValueError("Client certificate and key must be configured together")
        return self


def load_settings() -> ApiSettings:
    """Прочитать настройки установки заново: правка .env действует без перезапуска."""
    return ApiSettings(_env_file=ENV_FILE)
