"""Dedicated JSON-only proxy for the published Extractor HTTP API."""
import secrets
from ... import __version__
from fastapi import Depends, FastAPI, HTTPException, Header
from ...domain.epa.contract import SCHEMAS
from ...domain.epa.errors import ProjectsApiError
from ...infrastructure.onec_http import ProjectsApiClient
from ...infrastructure.settings import load_settings

def authorize(x_api_key: str = Header(default="")):
    expected = load_settings().proxy_key
    if not expected:
        raise HTTPException(503, "Proxy key is not configured")
    if not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid API key")


app = FastAPI(title="Extractor Projects API", version=__version__, dependencies=[Depends(authorize)])


def client():
    try:
        return ProjectsApiClient(load_settings())
    except ValueError as exc:
        raise HTTPException(503, "Projects API is not configured") from exc


def endpoint_for(route):
    def endpoint(body: SCHEMAS[route], api: ProjectsApiClient = Depends(client)):
        try:
            return api.call(route, body.model_dump())
        except ProjectsApiError as exc:
            if exc.rejected_before_write:
                raise HTTPException(exc.status, exc.rejection()) from None
            raise HTTPException(exc.status, str(exc)) from exc
    return endpoint


for route in SCHEMAS:
    app.add_api_route("/" + route, endpoint_for(route), methods=["POST"], name=route.replace("/", "_"))
