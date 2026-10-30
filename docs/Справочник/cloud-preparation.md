# Подготовка Extractor 1C MCP для облачных агентов

Команды Python и Docker выполняются из каталога `mcp/`; файлы Compose и `.env` находятся там же.

## Что уже реализовано

- удалённый Streamable HTTP MCP и отдельная проверка `readiness` выбранной базы;
- переносимый plugin bundle `mcp/plugins/extractor1c-cloud` для Codex и совместимый `.mcp.json` для Claude Code;
- SQLite-хранилище планов и операций на постоянном томе;
- связка preview/commit: сервер записывает точный payload, выдаёт `planId`, а commit принимает только `planId` и UUID `operationId`;
- повтор завершённой операции возвращает сохранённый ответ, а неопределённый результат запрещает слепой повтор;
- компактный поиск, фильтрация и пагинация метаданных, полей preview и состояния проекта;
- серверный анализ ссылок регистра накопления и связанных непериодических регистров сведений;
- встроенные MCP instructions для одинакового безопасного поведения клиентов.

## Сетевая схема

Для direct_http размещайте MCP в сети доступных HTTP-публикаций 1С; для dcc нужен доступ к DCC версии **2.0.2 или новее**. Список HTTP-баз задаётся YAML, список DCC-баз обнаруживается сервером. Настройки: [CONFIG](../../mcp/skills/extractor1c/CONFIG.md). Наружу публикуется только reverse proxy; порт `8000` контейнера в cloud-compose не пробрасывается.

`docker-compose.cloud.yml` и `deploy/Caddyfile.example` являются шаблоном следующего этапа. Для облачных Codex/Claude нужен публично разрешимый `EXTRACTOR1C_PUBLIC_HOST` и HTTPS. До включения публичного доступа можно запускать текущий локальный compose по HTTP с `EXTRACTOR_PROJECTS_ALLOW_HTTP_DEV=true`.

## Переменные

- `EXTRACTOR_PROJECTS_URL`, `USER`, `PASSWORD` — выделенная учётная запись API 1С;
- `EXTRACTOR_PROJECTS_MCP_TOKENS` — Bearer-токены для MCP;
- `EXTRACTOR1C_PUBLIC_HOST` — DNS-имя без схемы;
- `EXTRACTOR_PROJECTS_OPERATION_DB` — файл на постоянном томе;
- `EXTRACTOR_PROJECTS_CA_BUNDLE`, `CLIENT_CERT`, `CLIENT_KEY` — необязательные корпоративный CA и mTLS к 1С.

В клиенте задайте `EXTRACTOR1C_MCP_URL=https://<host>/mcp` и `EXTRACTOR1C_MCP_AUTHORIZATION=Bearer <токен>`. Секреты не коммитьте. Для полностью hosted-коннекторов, которые не поддерживают произвольный заголовок Authorization, следующим этапом потребуется OAuth/OIDC-фасад вместо статических Bearer-токенов.

## Порядок публикации

1. Обновить Экстрактор 1С начиная с 3.16.1.34 из коммерческой поставки [«Экстрактор данных 1С в BI»](https://bi.denvic.ru/), методы EPA_Projects 3.3.5 и MCP 3.3.6. Проверить отдельно mcpVersion, expectedOneCMethodsVersion, фактическую oneCMethodsVersion и capabilities через readiness.
2. Развернуть MCP в доступной к 1С сети с постоянным томом.
3. Настроить DNS/TLS и ограничение входящего трафика.
4. Проверить `/health`, затем MCP-инструмент `readiness`: он проверяет хранилище операций и доступ выбранного транспорта.
5. Установить plugin bundle либо перенести его `.mcp.json` в конфигурацию Claude.
6. Выполнить preview тестового проекта, подтвердить план, создать его и проверить через `verify_project_summary`.
