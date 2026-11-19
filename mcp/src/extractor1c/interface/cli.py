"""Команда ``extractor1c-mcp``: запуск сервера по stdio или streamable HTTP."""
import argparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extractor1c-mcp",
        description="Local MCP bridge to the fixed JSON web service in 1C.",
    )
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="streamable-http")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="Адрес для streamable-http; наружу публикуйте только через HTTPS-прокси")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Порт для streamable-http")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.transport == "stdio":
        from .mcp import tools  # noqa: F401  (импорт регистрирует инструменты)
        from .mcp.registry import mcp
        mcp.run(transport="stdio")
    else:
        import uvicorn

        from .http.app import app
        uvicorn.run(app, host=args.host, port=args.port)
