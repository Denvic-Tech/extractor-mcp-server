"""Базы 1С и маршрут до каждой: прямой HTTP или DCC. Автоматической замены маршрута нет."""
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from pathlib import Path

import requests
import yaml

from .dcc import DccClient
from .onec_http import ProjectsApiClient
from .settings import ApiSettings


@dataclass(frozen=True)
class Target:
    settings: ApiSettings
    base_id: str
    identity: str


def _identity(settings, base_id):
    # Do not expose credentials in IDs. Separate stores for different upstream users.
    endpoint = settings.dcc_url.rstrip("/") if settings.transport == "dcc" else settings.url.rstrip("/")
    if settings.transport == "dcc" and endpoint.endswith("/api"):
        endpoint = endpoint[:-4]
    value = "\0".join((settings.transport, endpoint,
                        settings.dcc_user if settings.transport == "dcc" else settings.user, base_id))
    return hashlib.sha256(value.encode()).hexdigest()


def _direct_targets(settings):
    bases = settings.bases
    if settings.bases_file:
        if bases:
            raise ValueError("Configure either bases or bases_file, not both")
        try:
            document = yaml.safe_load(Path(settings.bases_file).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            raise ValueError("Cannot load configured bases YAML") from None
        if not isinstance(document, dict) or set(document) != {"bases"} or not isinstance(document["bases"], list) or not document["bases"]:
            raise ValueError("Bases YAML requires a nonempty bases list")
        bases = document["bases"]
    if not bases:
        return [(settings.default_base_id or "default", "1C", settings)]
    result, seen = [], set()
    allowed = {"base_id", "name", "url", "user", "password", "allow_http_dev", "ca_bundle", "client_cert", "client_key", "timeout"}
    for item in bases:
        if not isinstance(item, dict) or set(item) - allowed or not isinstance(item.get("base_id"), str) or not item["base_id"].strip():
            raise ValueError("Each configured base requires a unique base_id and fixed connection settings")
        base_id = item["base_id"]
        if base_id in seen:
            raise ValueError("Duplicate base_id")
        seen.add(base_id)
        overrides = {k: v for k, v in item.items() if k not in {"name", "base_id"}}
        # Validate per-base TLS combinations; model_copy alone skips validation.
        config = ApiSettings.model_validate({**settings.model_dump(), **overrides})
        result.append((base_id, item.get("name", base_id), config))
    return result


def list_bases(settings):
    """Return safe public base descriptions, never URLs, passwords or tokens."""
    if settings.transport == "direct_http":
        return [{"baseId": key, "name": name, "transport": "direct_http"}
                for key, name, _ in _direct_targets(settings)]
    with DccClient(settings) as api:
        return api.list_bases()


def resolve_target(settings, base_id=None):
    """Resolve an explicit/default base, requiring selection for multiple bases."""
    selected = base_id or settings.default_base_id
    if settings.transport == "direct_http":
        choices = _direct_targets(settings)
    else:
        choices = [(item["baseId"], item["name"], settings) for item in list_bases(settings)]
    if not selected:
        if len(choices) != 1:
            raise ValueError("Select base_id from list_bases")
        selected = choices[0][0]
    for key, _, config in choices:
        if key == selected:
            return Target(config, key, _identity(config, key))
    raise ValueError("Unknown or inaccessible base_id")


def call_target(target, route, body, operation_id=None,
                on_accepted: Callable[[str, str, str], None] | None = None):
    if target.settings.transport == "direct_http":
        with requests.Session() as session:
            return ProjectsApiClient(target.settings, session=session).call(route, body)
    with DccClient(target.settings) as api:
        return api.call(target.base_id, route, body, operation_id, on_accepted=on_accepted)


def get_target_operation(target, operation_id):
    if target.settings.transport != "dcc":
        raise ValueError("DCC operation lookup requires DCC transport")
    with DccClient(target.settings) as api:
        return api.get_operation(target.base_id, operation_id)
