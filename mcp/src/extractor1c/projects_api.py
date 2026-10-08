# //(Денис 2026-10-03. Версия MCP 1.2.0
# Закрытые схемы области счетов и новые фиксированные маршруты чтения аналитики и версии 1С.
"""JSON-only client for the fixed 1C Projects API. No CodeExecutor fallback."""
import base64
import io
import json
from pathlib import Path
import tempfile
from typing import Literal
from urllib.parse import urlsplit
import zipfile

import requests
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, TypeAdapter, model_serializer, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROUTES = frozenset({"metadata/list", "metadata/describe", "connections/list", "projects/list",
                    "service/readiness", "accounts/subconto/describe",
                    "projects/preview", "projects/create", "projects/update", "projects/verify",
                    "projects/schedule/get", "projects/schedule/preview", "projects/schedule/set",
                    "projects/export/preview", "projects/export/start", "projects/export/status",
                    "projects/initialization/preview", "projects/initialization/start"})


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
    operation_db: str = Field(
        default_factory=lambda: str(Path(tempfile.gettempdir()) / "extractor1c-operations.sqlite3")
    )
    plan_ttl_seconds: int = Field(default=900, ge=60, le=86400)

    @model_validator(mode="after")
    def valid_tls_settings(self):
        if bool(self.client_cert) != bool(self.client_key):
            raise ValueError("Client certificate and key must be configured together")
        return self


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmptyRequest(StrictRequest):
    pass


class AccountScope(StrictRequest):
    codes: list[str] = Field(min_length=1, max_length=20)
    includeSubaccounts: StrictBool = True
    maxAccounts: StrictInt = Field(default=200, ge=1, le=1000)
    maxKinds: StrictInt = Field(default=1000, ge=1, le=5000)

    @model_validator(mode="after")
    def valid_codes(self):
        if any(not c.strip() or len(c) > 50 or any(ord(ch) < 32 for ch in c) for c in self.codes):
            raise ValueError("Invalid account code")
        if len(set(self.codes)) != len(self.codes):
            raise ValueError("Duplicate account codes")
        return self


class AccountAnalyticsRequest(AccountScope):
    object: str = Field(pattern=r"^(?:РегистрБухгалтерии|ПланСчетов)\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*$")


class ObjectRequest(StrictRequest):
    object: str = Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*){1,2}$", max_length=512)


class DescribeObjectRequest(StrictRequest):
    # Two first components identify a metadata object. Further components are
    # reference fields whose target object becomes the root for describe.
    object: str = Field(pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*)+$", max_length=512)


class ProjectName(StrictRequest):
    projectName: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_project_name(self):
        if not self.projectName.strip() or any(ord(char) < 32 for char in self.projectName):
            raise ValueError("Project name must be nonempty and contain no control characters")
        return self


class ProjectConnection(StrictRequest):
    # Omitted names are resolved before an MCP preview is stored as a plan.
    connectionName: str = Field(default="", max_length=150)

    @model_validator(mode="after")
    def valid_connection_name(self):
        if (self.connectionName and not self.connectionName.strip()) or any(
            ord(char) < 32 for char in self.connectionName
        ):
            raise ValueError("Invalid connection name")
        return self


# //(Денис 2026-10-03. Версия MCP 1.2.0
# Опциональная трёхролевая сегментация, закрытые схемы и совместимость старых запросов.
class Segmentation(StrictRequest):
    """One native grouping with independently selected execution roles (API 3.2.0)."""
    segment: Literal["По значению", "Первая буква", "Первые две буквы", "Произвольная подстрока",
                     "Час", "День", "Неделя", "Месяц", "Квартал", "Полугодие", "Год"]
    field: str = Field(min_length=1, max_length=512,
                       pattern=r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*)*$")
    regular: StrictBool
    extended: StrictBool
    initialization: StrictBool

    @model_validator(mode="after")
    def valid_roles(self):
        if not (self.regular or self.extended or self.initialization):
            raise ValueError("At least one segmentation role must be selected")
        return self
# \\\Денис 2026-10-03. Версия MCP 1.2.0)


class ProjectDefinition(ObjectRequest, ProjectName, ProjectConnection):
    tableName: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=100)
    segment: Literal["", "День", "Месяц", "Первые две буквы", "По значению"] = ""
    segmentation: list[Segmentation] | None = Field(default=None, max_length=500)
    periodicity: Literal["", "Час", "День", "Неделя", "Месяц", "Квартал", "Полугодие", "Период",
                         "Запись", "Регистратор", "Секунда", "Минута", "Декада", "Авто"] | None = None
    segmentField: str = Field(default="", max_length=512)
    guidColumnNames: dict[str, str] = Field(default_factory=dict)
    includeGuids: StrictBool = True
    includeMetadataTypes: StrictBool = True
    maxItemsPerBatch: StrictInt = Field(default=0, ge=0, le=1000000)
    selectedFields: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def segment_matches(self):
        # //(Денис 2026-10-03. Версия MCP 1.2.0
        # Один источник выбора: новый контракт не смешивается с прежними параметрами.
        if self.segmentation is not None:
            if self.segment or self.segmentField:
                raise ValueError("Do not combine segmentation with segment/segmentField")
            return self.valid_mapping_fields()
        # \\\Денис 2026-10-03. Версия MCP 1.2.0)
        self.valid_mapping_fields()
        import re
        field_path = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*)*"
        if self.segment in {"Первые две буквы", "По значению"}:
            if not re.fullmatch(field_path, self.segmentField):
                raise ValueError("Field segmentation requires a field path")
            return self
        if self.segmentField:
            raise ValueError("segmentField requires text or value segmentation")
        if self.object.startswith("Справочник.") != (self.segment == ""):
            raise ValueError("Catalog: empty segment; register history: День or Месяц")
        return self

    @model_serializer(mode="wrap")
    def api_payload(self, handler):
        """Do not emit mutually exclusive simple fields alongside a grouping array."""
        payload = handler(self)
        if self.segmentation is not None:
            payload.pop("segment", None)
            payload.pop("segmentField", None)
        if self.periodicity is None:
            payload.pop("periodicity", None)
        return payload

    def valid_mapping_fields(self):
        import re
        identifier = r"[A-Za-z_][A-Za-z0-9_]{0,99}"
        field_path = r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*(?:\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё_0-9]*)*"
        if any(len(name) > 512 or not re.fullmatch(field_path, name) for name in self.selectedFields):
            raise ValueError("Invalid selected field")
        if len(set(self.selectedFields)) != len(self.selectedFields):
            raise ValueError("Duplicate selected fields")
        if len(self.guidColumnNames) > 200 or any(
            not re.fullmatch(field_path, source) or not re.fullmatch(identifier, target)
            for source, target in self.guidColumnNames.items()
        ):
            raise ValueError("Invalid GUID column mapping")
        if len(set(self.guidColumnNames.values())) != len(self.guidColumnNames):
            raise ValueError("Duplicate GUID column names")
        return self


class GroupProjectDefinition(ProjectName, ProjectConnection):
    rows: list[ProjectDefinition] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def consistent_rows(self):
        names = {name for name in [self.connectionName, *(row.connectionName for row in self.rows)] if name}
        if len(names) > 1 or any(row.projectName != self.projectName for row in self.rows):
            raise ValueError("Rows must share project and connection")
        tables = [row.tableName.casefold() for row in self.rows]
        if len(set(tables)) != len(tables):
            raise ValueError("Duplicate target tables")
        return self


class ProjectUpdate(ProjectName):
    expectedState: str = Field(min_length=1, max_length=524288)
    comment: str = Field(max_length=1000)
    disabled: StrictBool
    includeGuids: StrictBool = False
    includeMetadataTypes: StrictBool = False


class ProjectScheduleRef(StrictRequest):
    projectGuid: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ProjectExportRef(ProjectScheduleRef):
    """An existing project, never executable code or an arbitrary HTTP URL."""


class ProjectExportStart(ProjectExportRef):
    expectedProjectState: str = Field(min_length=1, max_length=524288)


class ProjectExportStatus(ProjectExportRef):
    taskId: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class ScheduleDefinition(StrictRequest):
    """The first-stage scheduling subset; times belong to the 1C time context."""
    kind: Literal["daily", "weekly", "interval"]
    time: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")
    weekdays: list[StrictInt] | None = Field(default=None, min_length=1, max_length=7)
    intervalMinutes: StrictInt | None = Field(default=None, ge=1, le=1440)
    startTime: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")
    endTime: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.kind == "interval":
            if self.time is not None or self.weekdays is not None:
                raise ValueError("Interval schedules do not accept time or weekdays")
            if self.intervalMinutes is None or self.startTime is None or self.endTime is None:
                raise ValueError("Interval schedules require intervalMinutes, startTime and endTime")
            if self.startTime >= self.endTime:
                raise ValueError("Interval window must end after it starts, within the same day")
        else:
            if self.time is None:
                raise ValueError("Daily and weekly schedules require time")
            if any(value is not None for value in (self.intervalMinutes, self.startTime, self.endTime)):
                raise ValueError("Interval fields require kind=interval")
            if self.kind == "daily" and self.weekdays is not None:
                raise ValueError("Daily schedules do not accept weekdays")
            if self.kind == "weekly":
                if not self.weekdays or any(day < 1 or day > 7 for day in self.weekdays):
                    raise ValueError("Weekly schedules require weekdays 1 (Monday) to 7 (Sunday)")
                if len(set(self.weekdays)) != len(self.weekdays):
                    raise ValueError("Duplicate weekdays")
        return self


class ProjectSchedulePreview(ProjectScheduleRef):
    enabled: StrictBool
    schedule: ScheduleDefinition | None = None


class ProjectScheduleSet(ProjectSchedulePreview):
    expectedScheduleState: str = Field(min_length=1, max_length=524288)


class ProjectRowsAppend(ProjectName, ProjectConnection):
    expectedState: str = Field(min_length=1, max_length=524288)
    rows: list[ProjectDefinition] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def consistent_rows(self):
        names = {name for name in [self.connectionName, *(row.connectionName for row in self.rows)] if name}
        if len(names) > 1 or any(row.projectName != self.projectName for row in self.rows):
            raise ValueError("Rows must share project and connection")
        tables = [row.tableName.casefold() for row in self.rows]
        if len(set(tables)) != len(tables):
            raise ValueError("Duplicate target tables")
        return self


class ProjectRowChange(ProjectName):
    expectedState: str = Field(min_length=1, max_length=524288)
    currentTableName: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=100)
    definition: ProjectDefinition

    @model_validator(mode="after")
    def consistent_definition(self):
        if self.definition.projectName != self.projectName:
            raise ValueError("Definition must use the existing project")
        if self.definition.tableName.casefold() != self.currentTableName.casefold():
            raise ValueError("Renaming a target table is not supported")
        return self


# Project-level and row-level changes share the same public preview/update
# operations; the closed union still validates each concrete payload strictly.
ProjectChange = ProjectUpdate | ProjectRowsAppend | ProjectRowChange
ProjectPreview = ProjectDefinition | GroupProjectDefinition | ProjectRowsAppend | ProjectRowChange


SCHEMAS = {"metadata/list": EmptyRequest, "metadata/describe": DescribeObjectRequest,
           "service/readiness": EmptyRequest, "accounts/subconto/describe": AccountAnalyticsRequest,
           "connections/list": EmptyRequest, "projects/list": EmptyRequest,
           "projects/preview": ProjectPreview,
           "projects/create": ProjectDefinition | GroupProjectDefinition,
           "projects/update": ProjectChange, "projects/verify": ProjectName,
           "projects/schedule/get": ProjectScheduleRef,
           "projects/schedule/preview": ProjectSchedulePreview,
           "projects/schedule/set": ProjectScheduleSet,
           "projects/export/preview": ProjectExportRef,
           "projects/export/start": ProjectExportStart,
           "projects/export/status": ProjectExportStatus,
           "projects/initialization/preview": ProjectExportRef,
           "projects/initialization/start": ProjectExportStart}


LICENSE_ERRORS = {
    "LICENSE_REQUIRED": (403, "Требуется лицензия Экстрактора 1С."),
    "LICENSE_INVALID": (403, "Лицензия Экстрактора 1С не прошла штатную проверку."),
    "SUPPORT_REQUIRED": (403, "Для записи проекта требуется действующий срок поддержки dateDue."),
    "SUPPORT_EXPIRED": (403, "Срок поддержки лицензии истек. Продлите поддержку для записи проекта."),
    "LICENSE_CHECK_UNAVAILABLE": (503, "Не удалось подтвердить право записи через штатные методы лицензирования."),
}


class ProjectsApiError(RuntimeError):
    def __init__(self, status, message, *, code=None, write_applied=None):
        self.status = status
        self.code = code
        self.write_applied = write_applied
        super().__init__(message)

    @property
    def rejected_before_write(self):
        return (self.code in LICENSE_ERRORS and self.write_applied is False
                and self.status == LICENSE_ERRORS[self.code][0])

    def rejection(self):
        return {"httpStatus": self.status, "code": self.code,
                "error": str(self), "writeApplied": False}


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
        if route not in ROUTES:
            raise ValueError("Unknown API operation")
        payload = TypeAdapter(SCHEMAS[route]).validate_python(body).model_dump(exclude_none=True)
        auth = base64.b64encode(f"{self.settings.user}:{self.settings.password}".encode("utf-8")).decode("ascii")
        # No automatic retry and no redirect carrying Basic credentials.
        response = self.session.post(
            self.settings.url.rstrip("/") + "/" + route, json=payload,
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
            if isinstance(result, dict) and isinstance(result.get("code"), str):
                known = LICENSE_ERRORS.get(result["code"])
                if (route in {"projects/create", "projects/update", "projects/schedule/set"} and known
                        and response.status_code == known[0] and result.get("writeApplied") is False):
                    # Never expose arbitrary upstream text or classify a generic 403/503 as safe.
                    raise ProjectsApiError(known[0], known[1], code=result["code"], write_applied=False)
            raise ProjectsApiError(response.status_code, "Projects API request failed; verify state before retry")
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
# \\\Денис 2026-10-03. Версия MCP 1.2.0)
