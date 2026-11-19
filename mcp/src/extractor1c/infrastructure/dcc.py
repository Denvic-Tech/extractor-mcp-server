"""Асинхронная передача запросов в 1С через DCC v2.

Маршруты операций DCC заданы контрактом mcp-operations-api. Ни один запрос этого
модуля не заставляет DCC самостоятельно обращаться к 1С по HTTP.
"""
from collections.abc import Callable
import time
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import requests

from ..domain.epa.contract import validate_request
from ..domain.epa.errors import DccOperationPending, ProjectsApiError
from ..domain.epa.relay import operation_result, relay_result


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

    def _request(self, method, path, *, _retry_auth=True, **kwargs):
        try:
            response = self.session.request(method, self.url + path, timeout=self.settings.timeout,
                allow_redirects=False, verify=self.settings.ca_bundle or True,
                cert=(self.settings.client_cert, self.settings.client_key) if self.settings.client_cert else None,
                **kwargs)
        except requests.RequestException:
            raise ProjectsApiError(502, "DCC transport unavailable; verify operation state before retry") from None
        # An expired token is rejected before the route executes. Refresh once;
        # an operation submission retains its original body and Idempotency-Key.
        if response.status_code == 401 and _retry_auth and kwargs.get("headers", {}).get("Authorization", "").startswith("Bearer "):
            self.token = None
            headers = {**kwargs["headers"], **self._headers()}
            return self._request(method, path, _retry_auth=False, **{**kwargs, "headers": headers})
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
        return relay_result(payload, route)

    def call(self, base_id, route, body, operation_id=None,
             on_accepted: Callable[[str, str, str], None] | None = None):
        payload = validate_request(route, body)
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
        if on_accepted is not None:
            # Persist the accepted task before the first GET. A process crash
            # during waiting must not discard its identity.
            on_accepted(operation_id, route, accepted["task_id"])
        deadline = time.monotonic() + self.settings.dcc_wait_timeout
        while True:
            try:
                state = self.get_operation(base_id, operation_id)
                if state["task_id"] != accepted["task_id"]:
                    raise ProjectsApiError(502, "DCC operation task identity changed")
            except ProjectsApiError as exc:
                if exc.status >= 500:
                    raise DccOperationPending(operation_id, accepted["task_id"]) from None
                raise
            try:
                ready, result = operation_result(state, route)
            except DccOperationPending:
                raise DccOperationPending(operation_id, accepted["task_id"]) from None
            if ready:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DccOperationPending(operation_id, accepted["task_id"])
            time.sleep(min(self.settings.dcc_poll_interval, remaining))
