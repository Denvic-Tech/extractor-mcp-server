"""Fixed API transports: direct 1C HTTP or asynchronous DCC relay.

DCC operation routes are the contract in DCC/v2/MCP_DCC2/ТЗ/DCC/mcp-operations-api-contract.md;
they require a corresponding DCC deployment. No request from this module makes
DCC initiate an HTTP request to 1C.
"""
from dataclasses import dataclass
import hashlib
import time
from pathlib import Path

import yaml
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import requests
from pydantic import TypeAdapter

from .projects_api import ApiSettings, LICENSE_ERRORS, ProjectsApiClient, ProjectsApiError, ROUTES, SCHEMAS


@dataclass(frozen=True)
class Target:
    settings: ApiSettings
    base_id: str
    identity: str


class DccOperationPending(ProjectsApiError):
    """Waiting expired or submission outcome is unknown; resume the same ID."""
    def __init__(self, operation_id):
        self.operation_id = operation_id
        super().__init__(202, "DCC operation pending; query the same operation ID before retry")


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


def call_target(target, route, body, operation_id=None):
    if target.settings.transport == "direct_http":
        with requests.Session() as session:
            return ProjectsApiClient(target.settings, session=session).call(route, body)
    with DccClient(target.settings) as api:
        return api.call(target.base_id, route, body, operation_id)


def get_target_operation(target, operation_id):
    if target.settings.transport != "dcc":
        raise ValueError("DCC operation lookup requires DCC transport")
    with DccClient(target.settings) as api:
        return api.get_operation(target.base_id, operation_id)


def operation_result(state, route):
    """Decode a stored DCC result with the same licensing checks as a live call."""
    if route not in ROUTES:
        raise ValueError("Unknown API operation")
    if state.get("method") is not None and state["method"] != route:
        raise ProjectsApiError(502, "DCC operation method mismatch")
    return DccClient._result(state, route)


class DccClient:
    def __init__(self, settings, session=None):
        self.settings = settings
        parsed = urlsplit(settings.dcc_url)
        if (parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/api"}):
            raise ValueError("Configure the fixed DCC root or /api URL")
        if parsed.scheme != "https" and not settings.allow_http_dev:
            raise ValueError("HTTPS required; HTTP may only be enabled explicitly for dev")
        if not settings.dcc_user or not settings.dcc_password:
            raise ValueError("Dedicated DCC credentials required")
        self.url = settings.dcc_url.rstrip("/") + ("" if parsed.path.rstrip("/") == "/api" else "/api")
        self.session = session or requests.Session()
        self._owns_session = session is None
        self.session.trust_env = False
        self.token = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._owns_session:
            self.session.close()

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.url + path, timeout=self.settings.timeout,
                allow_redirects=False, verify=self.settings.ca_bundle or True,
                cert=(self.settings.client_cert, self.settings.client_key) if self.settings.client_cert else None,
                **kwargs)
        except requests.RequestException:
            raise ProjectsApiError(502, "DCC transport unavailable; verify operation state before retry") from None
        try:
            data = response.json()
        except ValueError:
            raise ProjectsApiError(502, "Invalid DCC response") from None
        if not 200 <= response.status_code < 300:
            raise ProjectsApiError(response.status_code, "DCC request rejected; verify operation state before retry")
        if isinstance(data, dict) and (data.get("success") is False or data.get("error") is not None):
            raise ProjectsApiError(502, "DCC returned an unsuccessful response")
        return data

    def _headers(self):
        if self.token is None:
            result = self._request("POST", "/v2/users/login", data={"grant_type": "password", "username": self.settings.dcc_user,
                                                                    "password": self.settings.dcc_password})
            # The public login uses an envelope; accept its explicit token field.
            payload = result.get("data", result) if isinstance(result, dict) else {}
            self.token = payload.get("access_token")
            if not isinstance(self.token, str) or not self.token:
                raise ProjectsApiError(502, "DCC login returned no access token")
        return {"Authorization": "Bearer " + self.token, "Accept": "application/json"}

    def list_bases(self):
        result, seen, offset = [], set(), 0
        while True:
            envelope = self._request("GET", "/v2/connectors/", headers=self._headers(),
                                     params={"page_size": 100, "offset": offset})
            rows = envelope.get("data") if isinstance(envelope, dict) else None
            if not isinstance(rows, list):
                raise ProjectsApiError(502, "Invalid DCC connector list")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id_connector"), str):
                    raise ProjectsApiError(502, "Invalid DCC connector identity")
                try:
                    key = str(UUID(row["id_connector"]))
                except ValueError:
                    raise ProjectsApiError(502, "Invalid DCC connector identity") from None
                if key in seen:
                    raise ProjectsApiError(502, "DCC connector pagination repeated an identity")
                seen.add(key)
                # Only extractors registered for DCC v2 with the MCP task adapter
                # are routable. Offline supported bases stay discoverable.
                if row.get("client_contract") != "extractor-1c/2.0" or not isinstance(row.get("supported_commands"), list) or "mcp" not in row["supported_commands"]:
                    continue
                result.append({"baseId": key, "name": row.get("name", key), "transport": "dcc",
                               "status": row.get("last_status"), "health": row.get("last_health_status"),
                               "healthAt": row.get("last_health_at"), "statusAt": row.get("last_status_dt"),
                               "supportedCommands": row.get("supported_commands"), "clientContract": row.get("client_contract")})
            total = envelope.get("meta", {}).get("total")
            offset += len(rows)
            if not rows or (isinstance(total, int) and offset >= total) or (total is None and len(rows) < 100):
                return result

    @staticmethod
    def _path(base_id, operation_id):
        # UUID operations prevent path traversal and accidental new identities.
        operation_id = str(UUID(operation_id))
        return "/v2/connectors/" + quote(base_id, safe="") + "/mcp/operations/" + operation_id

    def get_operation(self, base_id, operation_id):
        envelope = self._request("GET", self._path(base_id, operation_id), headers=self._headers())
        payload = envelope.get("data", envelope) if isinstance(envelope, dict) else None
        if not isinstance(payload, dict) or payload.get("operation_id") != str(UUID(operation_id)):
            raise ProjectsApiError(502, "Invalid DCC operation identity")
        self._validate_task_identity(payload)
        return payload

    @staticmethod
    def _validate_task_identity(payload):
        try:
            UUID(payload["task_id"])
        except (KeyError, ValueError, TypeError, AttributeError):
            raise ProjectsApiError(502, "Invalid DCC task identity") from None
        if payload.get("status") not in {"queued", "running", "completed", "failed", "recovery_required", "cancelled", "uncertain"}:
            raise ProjectsApiError(502, "Invalid DCC operation status")

    @staticmethod
    def _result(payload, route):
        if payload.get("status") in {"recovery_required", "uncertain"}:
            raise DccOperationPending(payload["operation_id"])
        if payload.get("status") == "cancelled":
            raise ProjectsApiError(409, "DCC operation cancelled; verify state before retry")
        if payload.get("status") not in {"completed", "failed"}:
            return False, None
        if payload.get("result_ready") is not True:
            return False, None
        status, result = payload.get("http_status"), payload.get("result")
        if type(status) is not int or not isinstance(result, (dict, list)):
            raise ProjectsApiError(502, "DCC operation has no structured 1C result")
        if status != 200:
            known = LICENSE_ERRORS.get(result.get("code")) if isinstance(result, dict) else None
            if (known and route in {"projects/create", "projects/update", "projects/schedule/set"}
                    and status == known[0] and result.get("writeApplied") is False):
                raise ProjectsApiError(status, known[1], code=result["code"], write_applied=False)
            raise ProjectsApiError(status, "1C operation failed; verify state before retry")
        return True, result

    def call(self, base_id, route, body, operation_id=None):
        if route not in ROUTES:
            raise ValueError("Unknown API operation")
        payload = TypeAdapter(SCHEMAS[route]).validate_python(body).model_dump(exclude_none=True)
        operation_id = str(UUID(operation_id)) if operation_id else str(uuid4())
        path = self._path(base_id, operation_id).rsplit("/", 1)[0]
        headers = {**self._headers(), "Idempotency-Key": operation_id}
        try:
            response = self._request("POST", path, headers=headers,
                                     json={"operation_id": operation_id, "method": route, "payload": payload})
            accepted = response.get("data", response) if isinstance(response, dict) else None
            if not isinstance(accepted, dict) or accepted.get("operation_id") != operation_id:
                raise DccOperationPending(operation_id)
            self._validate_task_identity(accepted)
        except ProjectsApiError as exc:
            if exc.status >= 500:
                raise DccOperationPending(operation_id) from None
            raise
        deadline = time.monotonic() + self.settings.dcc_wait_timeout
        while True:
            try:
                state = self.get_operation(base_id, operation_id)
            except ProjectsApiError as exc:
                if exc.status >= 500:
                    raise DccOperationPending(operation_id) from None
                raise
            ready, result = operation_result(state, route)
            if ready:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DccOperationPending(operation_id)
            time.sleep(min(self.settings.dcc_poll_interval, remaining))
