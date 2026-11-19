"""Сервер FastMCP и декораторы, которыми инструменты в нём регистрируются."""
import asyncio
from contextlib import contextmanager
from functools import wraps
import inspect
import os
from typing import Callable, ParamSpec, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from ... import __version__
from ...application.errors import ApplicationError, LicenseRejected
from . import runtime
from .instructions import SERVER_INSTRUCTIONS

# FastMCP при host=127.0.0.1 сам включает localhost-only DNS-rebinding защиту
# (allowed_hosts=127.0.0.1/localhost), из-за чего опубликованный по внешнему хосту
# эндпоинт /mcp отвечает 421 Invalid Host header. Задаём защиту явно: по умолчанию
# выключена (наружу торчит только Caddy, контейнер — во внутренней сети), но её
# можно ужесточить, перечислив разрешённые Host в EXTRACTOR_PROJECTS_ALLOWED_HOSTS
# через запятую, напр. "mcp.example.test". Caddy пробрасывает исходный Host, так
# что при включении значение должно совпадать с доменом, иначе будет 421.
_allowed_hosts = [h.strip() for h in os.getenv("EXTRACTOR_PROJECTS_ALLOWED_HOSTS", "").split(",") if h.strip()]
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=bool(_allowed_hosts),
    allowed_hosts=_allowed_hosts,
    allowed_origins=[f"http://{h}" for h in _allowed_hosts] + [f"https://{h}" for h in _allowed_hosts],
)

mcp = FastMCP("extractor1c", instructions=SERVER_INSTRUCTIONS,
              host="127.0.0.1", port=8001,
              stateless_http=True, json_response=True,
              transport_security=_transport_security)
# У FastMCP нет публичного параметра версии сервера: без этой строки клиент
# увидел бы версию библиотеки mcp. Совместимость закрепляет tests/test_tool_contract.py.
mcp._mcp_server.version = __version__

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)

_Parameters = ParamSpec("_Parameters")
_Result = TypeVar("_Result")


class LicenseRejectedToolError(ToolError):
    """Confirmed refusal by the 1C license gate, before any project/table write."""

    def __init__(self, result):
        self.result = result
        super().__init__(str(LicenseRejected(result)))


@contextmanager
def reported_to_client():
    """Показать отказ сценария клиенту MCP как ``ToolError`` с тем же текстом."""
    try:
        yield
    except LicenseRejected as exc:
        raise LicenseRejectedToolError(exc.result) from None
    except ApplicationError as exc:
        raise ToolError(str(exc)) from None


def tool_errors(function: Callable[_Parameters, _Result]) -> Callable[_Parameters, _Result]:
    """Обернуть функцию интерфейса так, чтобы отказы сценариев доходили до клиента."""
    @wraps(function)
    def guarded(*args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _Result:
        with reported_to_client():
            return function(*args, **kwargs)

    guarded._reports_tool_errors = True
    return guarded


def _forbid_unknown_arguments(name: str) -> None:
    """Закрыть схему аргументов инструмента до любого обращения к 1С.

    Модель аргументов FastMCP по умолчанию молча отбрасывает лишние поля, а
    публичной настройки для этого нет, поэтому правим созданную модель.
    """
    registered = mcp._tool_manager.get_tool(name)
    assert registered is not None
    argument_model = registered.fn_metadata.arg_model
    argument_model.model_config = {**argument_model.model_config, "extra": "forbid"}
    argument_model.model_rebuild(force=True)
    registered.parameters = argument_model.model_json_schema()


def threaded_tool(
    *, annotations: ToolAnnotations, forbid_unknown: bool = False,
) -> Callable[[Callable[_Parameters, _Result]], Callable[_Parameters, _Result]]:
    """Register blocking tools off the event loop; keep direct Python calls intact.

    DCC polling uses synchronous HTTP and sleep. ContextVars propagate into the
    worker, keeping the authenticated principal and selected base isolated.
    """
    def register(function: Callable[_Parameters, _Result]) -> Callable[_Parameters, _Result]:
        guarded = function if getattr(function, "_reports_tool_errors", False) else tool_errors(function)
        # FastMCP читает аннотации в контексте модуля-обёртки; передаём уже вычисленные.
        guarded.__signature__ = inspect.signature(guarded, eval_str=True)

        @wraps(guarded)
        async def execute(*args: _Parameters.args, **kwargs: _Parameters.kwargs) -> _Result:
            return await asyncio.to_thread(guarded, *args, **kwargs)

        mcp.tool(annotations=annotations)(execute)
        if forbid_unknown:
            _forbid_unknown_arguments(function.__name__)
        return guarded

    return register


def base_tool(function):
    """Expose base selection and bind all nested calls to one authorized target."""
    @wraps(function)
    def wrapped(*args, base_id: str | None = None, **kwargs):
        with reported_to_client():
            target = runtime.select_target(base_id)
            token = runtime._target.set(target)
            try:
                return function(*args, **kwargs)
            finally:
                runtime._target.reset(token)

    signature = inspect.signature(function, eval_str=True)
    wrapped.__signature__ = signature.replace(parameters=[*signature.parameters.values(),
        inspect.Parameter("base_id", inspect.Parameter.KEYWORD_ONLY, default=None,
                          annotation=str | None)])
    wrapped.__annotations__ = {**inspect.get_annotations(function, eval_str=True), "base_id": str | None}
    wrapped._reports_tool_errors = True
    return wrapped
