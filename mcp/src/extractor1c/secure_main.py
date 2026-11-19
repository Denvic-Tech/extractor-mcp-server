"""Точка входа JSON-прокси опубликованного API 1С: ``uvicorn extractor1c.secure_main:app``."""
from .interface.http.proxy import app, authorize, client, endpoint_for  # noqa: F401
