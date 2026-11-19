"""Среда выполнения сервера: настройки, выбранная база, журнал операций и маршрут до 1С.

Единственное место, где порты приложения связываются с конкретными адаптерами.
Выбранная база и идентификатор операции живут в ``ContextVar``: каждый запрос
и каждый рабочий поток видят только свои значения.
"""
from contextvars import ContextVar
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import requests

from ...application.errors import ApplicationError, GatewayError, LicenseRejected
from ...domain.epa.errors import DccOperationPending, ProjectsApiError
from ...infrastructure import settings as config
from ...infrastructure.dcc_metadata import DccMetadataClient, MetadataResource
from ...infrastructure.expiring_keys import ExpiringKeys
from ...infrastructure.onec_http import ProjectsApiClient
from ...infrastructure.operation_store import OperationStore
from ...infrastructure.settings import ApiSettings
from ...infrastructure.targets import Target, call_target, get_target_operation, list_bases, resolve_target
from ..http.auth import authenticated_principal

_target: ContextVar[Target | None] = ContextVar("extractor_target", default=None)
_operation_id: ContextVar[str | None] = ContextVar("extractor_operation_id", default=None)

# A capability read is an optimization, never authority to write. Native EPA
# still checks the actual receiver immediately before side effects, including
# after an extension downgrade. Keys contain only existing scoped identities;
# no credential, endpoint or response is persisted or exposed in this cache.
_direct_capability_keys = ExpiringKeys(ttl=60.0, capacity=256)
_direct_capabilities = _direct_capability_keys.entries


def settings() -> ApiSettings:
    """Настройки установки; читаются при каждом вызове, поэтому правка .env действует сразу."""
    return ApiSettings(_env_file=config.ENV_FILE)


def bases() -> list[dict]:
    try:
        return list_bases(settings())
    except (ValueError, ProjectsApiError, requests.RequestException):
        raise ApplicationError("Cannot list bases; check transport configuration and access") from None


def select_target(base_id: str | None) -> Target:
    try:
        return resolve_target(settings(), base_id)
    except (ValueError, ProjectsApiError, requests.RequestException):
        raise ApplicationError("Cannot select base; call list_bases and choose an accessible base_id") from None


def _capability_key() -> tuple[str, str] | None:
    target = _target.get()
    return (target.identity, authenticated_principal.get()) if target is not None else None


def _forget_direct_capability() -> None:
    _direct_capability_keys.discard(_capability_key())


def _remember_direct_capability(build: Any) -> None:
    capabilities = build.get("capabilities") if isinstance(build, dict) else None
    key = _capability_key()
    if key is None or not isinstance(capabilities, list) or "direct_sql_operations" not in capabilities:
        return
    _direct_capability_keys.add(key)


def _has_direct_capability() -> bool:
    return _capability_key() in _direct_capability_keys


@lru_cache(maxsize=256)
def _operation_store(path: str) -> OperationStore:
    return OperationStore(path)


def operation_store() -> OperationStore:
    """Журнал операций выбранной базы и вызывающего клиента."""
    path = Path(settings().operation_db).expanduser()
    if not path.is_absolute():
        # Anchor state to the installation config, independent of an AI
        # client's working directory or the directory used to start Python.
        path = config.ENV_FILE.parent / path
    path = path.resolve()
    target = _target.get()
    principal = authenticated_principal.get()
    if target is not None:
        digest = sha256((target.identity + "\0" + principal).encode()).hexdigest()
        return _operation_store(str(path.with_name(path.stem + "-" + digest + path.suffix)))
    return _operation_store(str(path))


def call_api(route: str, body: dict) -> Any:
    """Вызвать маршрут EPA выбранной базы; отказ 1С становится ``GatewayError``."""
    current = settings()
    try:
        target = _target.get()
        if target is not None:
            on_accepted = operation_store().remember_relay if target.settings.transport == "dcc" else None
            return call_target(target, route, body, operation_id=_operation_id.get(), on_accepted=on_accepted)
        with requests.Session() as session:
            session.trust_env = False
            return ProjectsApiClient(current, session).call(route, body)
    except DccOperationPending as exc:
        operation_store().remember_relay(exc.operation_id, route, exc.task_id)
        raise GatewayError(
            f"DCC operation pending: operation_id={exc.operation_id}; query get_operation_status on the same base_id.",
            status=exc.status) from None
    except ProjectsApiError as exc:
        if exc.rejected_before_write:
            raise LicenseRejected(exc.rejection()) from None
        details = f"1C API HTTP {exc.status}; {exc}"
        if exc.code:
            details += f"; code={exc.code}"
        if exc.error_id:
            details += f"; errorId={exc.error_id}"
        if exc.error_details:
            details += "; " + json.dumps(exc.error_details, ensure_ascii=False)
        raise GatewayError(details, status=exc.status, code=exc.code, error_id=exc.error_id) from None
    except requests.RequestException as exc:
        raise GatewayError(f"1C connection failed ({type(exc).__name__}).") from None


def relay_operation(operation_id: str) -> dict[str, Any]:
    """Состояние операции в DCC для выбранной базы."""
    try:
        return get_target_operation(_target.get(), operation_id)
    except requests.RequestException:
        raise ProjectsApiError(502, "DCC transport unavailable; verify operation state before retry") from None


def _read_dcc_metadata(
    resource: MetadataResource,
    snapshot_id: str | None = None,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the selected DCC base's snapshot; errors never select another route."""
    target = _target.get()
    if target is None or target.settings.transport != "dcc":
        raise ApplicationError("Metadata snapshots require DCC transport")
    try:
        with DccMetadataClient(target.settings) as api:
            return api.read(target.base_id, resource, snapshot_id=snapshot_id, params=params, body=body)
    except (ValueError, ProjectsApiError) as exc:
        status = exc.status if isinstance(exc, ProjectsApiError) else 422
        raise GatewayError(
            f"DCC metadata HTTP {status}; check snapshot and parameters; no transport fallback.",
            status=status, code="DCC_METADATA_UNAVAILABLE") from None
