"""HTTP-приложение сервера: эндпоинт MCP ``/mcp``, проба ``/health`` и проверка токена."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ... import __version__
from ..mcp import tools  # noqa: F401  (импорт регистрирует инструменты)
from ..mcp.registry import mcp
from ..mcp.tools.service import health
from .auth import BearerAuthMiddleware

mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Extractor 1C MCP", version=__version__, lifespan=lifespan)
app.get("/health")(health)
app.mount("/", mcp_app)
# Bearer-token guard for every path except /health (used by the container probe).
# Tokens come from EXTRACTOR_PROJECTS_MCP_TOKENS; unset means fail-closed.
app.add_middleware(BearerAuthMiddleware)
