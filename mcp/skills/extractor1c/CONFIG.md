# Настройка MCP 3.2.0

MCP читает `.env` из каталога `mcp/`. Сервер слушает `127.0.0.1:8001/mcp`; клиент передаёт `Authorization: Bearer <токен>` из `EXTRACTOR_PROJECTS_MCP_TOKENS`. Пароли и заголовки не печатай в команды, логи или навык.

## Прямой HTTP: несколько баз

```dotenv
EXTRACTOR_PROJECTS_TRANSPORT=direct_http
EXTRACTOR_PROJECTS_BASES_FILE=C:/secure/extractor-bases.yaml
EXTRACTOR_PROJECTS_DEFAULT_BASE_ID=erp
```

YAML имеет единственный корневой ключ `bases`, содержащий непустой список:

```yaml
bases:
  - base_id: erp
    name: ERP
    url: https://host/erp/hs/extractor-projects/v1
    user: service-user
    password: ""
    timeout: 60
    allow_http_dev: false
  - base_id: accounting
    name: Accounting
    url: https://host/accounting/hs/extractor-projects/v1
    user: service-user
    password: ""
```

Разрешённые поля базы: `base_id`, `name`, `url`, `user`, `password`, `allow_http_dev`, `ca_bundle`, `client_cert`, `client_key`, `timeout`. `base_id` уникален; сертификат клиента и ключ задаются вместе. Пустой пароль допустим, если так настроена публикация. YAML с реальными паролями хранится вне Git. Относительный путь к YAML считается от рабочего каталога процесса; для службы используйте абсолютный путь. `EXTRACTOR_PROJECTS_BASES_FILE` нельзя совмещать с `EXTRACTOR_PROJECTS_BASES`.

Для одной базы остаётся настройка `EXTRACTOR_PROJECTS_URL`, `EXTRACTOR_PROJECTS_USER`, `EXTRACTOR_PROJECTS_PASSWORD`. После подключения вызови `list_bases`, выбери `base_id`, передавай его в операции. База по умолчанию задаётся `EXTRACTOR_PROJECTS_DEFAULT_BASE_ID`.

## Через DCC v2

```dotenv
EXTRACTOR_PROJECTS_TRANSPORT=dcc
EXTRACTOR_PROJECTS_DCC_URL=https://dcc.example
EXTRACTOR_PROJECTS_DCC_USER=service-user
EXTRACTOR_PROJECTS_DCC_PASSWORD=<секрет из защищённого хранилища>
EXTRACTOR_PROJECTS_DCC_WAIT_TIMEOUT=60
EXTRACTOR_PROJECTS_DCC_POLL_INTERVAL=1
```

Список баз загружается из DCC, а не из YAML HTTP-публикаций. Включаются авторизованные коннекторы с `client_contract=extractor-1c/2.0` и командой `mcp` в `supported_commands`, в том числе временно offline. MCP работает с DCC; Экстрактор опрашивает DCC и возвращает результат задачи. Автоматического перехода на прямую публикацию 1С нет. Для работы необходимы серверные методы DCC, описанные в ТЗ интеграции; наличие адаптера MCP само по себе не подтверждает их развёртывание.

Проверяй `readiness` выбранной базы и опубликованную версию методов 3.2.0. Развёртывание: [архитектура](references/20-deployment-architecture.md).
