"""Прямой HTTP-клиент фиксированного Projects API 1С. Только JSON, без выполнения кода."""
import base64
import io
import json
from urllib.parse import urlsplit
import zipfile

import requests

from ..domain.epa.contract import validate_request
from ..domain.epa.errors import ProjectsApiError, upstream_failure
from .settings import ApiSettings


class ProjectsApiClient:
    def __init__(self, settings: ApiSettings, session=None):
        self.settings = settings
        parsed = urlsplit(settings.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.rstrip("/").endswith("/hs/extractor-projects/v1")):
            raise ValueError("Configure the fixed Projects API URL")
        if parsed.scheme != "https" and not settings.allow_http_dev:
            raise ValueError("HTTPS required; HTTP may only be enabled explicitly for dev")
        if not settings.user:
            raise ValueError("Dedicated 1C user required")
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.verify = self.settings.ca_bundle or True
        self.cert = ((self.settings.client_cert, self.settings.client_key)
                     if self.settings.client_cert else None)

    def call(self, route: str, body: dict):
        payload = validate_request(route, body)
        # Reuse the published verify URL without exposing a client-controlled router.
        wire_route = route
        if route == "projects/receiver/inspect":
            wire_route = "projects/verify"
            payload = {**payload, "receiverInspection": True}
        auth = base64.b64encode(f"{self.settings.user}:{self.settings.password}".encode("utf-8")).decode("ascii")
        # No automatic retry and no redirect carrying Basic credentials.
        response = self.session.post(
            self.settings.url.rstrip("/") + "/" + wire_route, json=payload,
            headers={"Authorization": "Basic " + auth, "Accept": "application/json"},
            timeout=self.settings.timeout, allow_redirects=False,
            verify=self.verify, cert=self.cert,
        )
        try:
            result = self._decode_response(response)
        except (ValueError, UnicodeError, zipfile.BadZipFile) as exc:
            if response.status_code != 200:
                raise ProjectsApiError(response.status_code, "Projects API request failed; verify state before retry") from None
            raise ProjectsApiError(502, "Invalid JSON from Projects API; verify state before retry") from exc
        if response.status_code != 200:
            # Never expose arbitrary upstream text or classify a generic 403/503 as safe.
            raise upstream_failure(route, response.status_code, result,
                                   "Projects API request failed; verify state before retry")
        return result

    @staticmethod
    def _decode_response(response):
        if response.headers.get("X-Extractor-Content-Encoding", "").casefold() == "zip+base64":
            # Base64Строка on older 1C versions may wrap long output.
            archive_bytes = base64.b64decode(b"".join(response.content.split()), validate=True)
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                files = [entry for entry in archive.infolist() if not entry.is_dir()]
                if len(files) != 1:
                    raise ValueError("Expected one JSON file in response archive")
                return json.loads(archive.read(files[0]).decode("utf-8-sig"))
        return response.json()
