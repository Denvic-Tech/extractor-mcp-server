# MCP Экстрактора 1С 3.3.7

Поддерживается Экстрактор 1С начиная с **3.16.1.34**, версия методов
EPA_Projects — **3.3.5**. После установки проверяйте фактическую версию методов
и capabilities выбранной базы через `readiness`. Для маршрута `dcc`
поддерживается DCC начиная с **2.0.2**.

Python-проект: `src/extractor1c`; тесты: `tests`; инструкции: `skills/extractor1c`; клиентский навык: `plugins/extractor1c-cloud/skills/extractor1c`.

Подробная [инструкция установки](../README.md) описывает подготовку 1С, публикацию HTTP-сервисов расширений без DCC, Docker на Unix/Windows, локальный Python, `.env`, `bases.yaml` и подключение ИИ-клиента.

Пошаговый установщик из `mcp/` (по умолчанию одна база без DCC): `py -3 scripts/install.py` на Windows или `python3 scripts/install.py` на Unix. Python 3.11+. Существующие настройки сохраняются; `--configure-only` создаёт только конфигурацию.

Ручная установка, все команды из `mcp/`:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path bases.yaml)) { Copy-Item bases.example.yaml bases.yaml }
# Заполните настройки и MCP-токен перед запуском.
.\.venv\Scripts\extractor1c-mcp.exe
.\.venv\Scripts\python.exe -m pytest -q
```

Существующий `.env` не перезаписывайте. [Транспорт и базы](skills/extractor1c/CONFIG.md), [общая документация](../README.md), [контракт](../docs/Справочник/mcp-server.md). Методы 1С сопровождаются в коммерческой поставке [«Экстрактор данных 1С в BI»](https://bi.denvic.ru/).

[JSON-контракт подготовки проектов](../docs/Справочник/project-template-contract.md):
`get_project_template_schema`, `validate_project_template`, `preview_project_template`.
Сохранение использует прежний `create_project` и его `plan_id/operation_id`.

## Пользовательские навыки

Администратор установки добавляет свои навыки в `skills/.user-skills/`:
каждый навык размещается в отдельной подпапке с `SKILL.md` в UTF-8.
`list_skills` показывает их вместе со встроенными навыками;
`get_skill` читает навык по ID `.user-skills/<имя-папки>` и его Markdown-документы.
Изменения файлов доступны без перезапуска MCP. В поставляемых Compose-файлах
папка подключена с хоста только для чтения.

[Структура, пример навыка, вызовы методов и настройка Docker](../docs/Справочник/user-skills.md).
