# Экстрактор 1С — образ MCP-сервера (FastAPI/uvicorn поверх Projects API 1С).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/mcp/src

WORKDIR /app/mcp

# Сначала зависимости — лучше кэшируется между сборками.
# requirements-mcp.txt подтягивает requirements.txt через "-r", поэтому копируем оба.
COPY mcp/requirements.txt mcp/requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements-mcp.txt

# Код приложения (пароль/секреты внутрь образа НЕ кладём — приходят из окружения).
COPY mcp/src ./src
COPY mcp/scripts ./scripts
COPY mcp/skills ./skills
COPY mcp/plugins ./plugins

EXPOSE 8000

# MCP-эндпоинт: /mcp, health: /health. За TLS-прокси (Caddy) uvicorn должен
# доверять X-Forwarded-* только от прокси. Порт 8000 наружу не публикуется —
# контейнер доступен лишь во внутренней docker-сети, поэтому forwarded-allow-ips=*
# здесь безопасен (единственный источник запросов — Caddy).
CMD ["uvicorn", "extractor1c.mcp_server:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
