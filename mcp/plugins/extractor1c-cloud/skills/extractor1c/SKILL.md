---
name: extractor1c
description: Create, inspect, verify and update Extractor 1C projects, run exports and manage schedules through MCP; develop and validate Extractor MCP and 1C API methods with mandatory regression tests.
---

# Extractor 1C Cloud

When developing or fixing Extractor MCP or its 1C API, read
[development-tests.md](references/development-tests.md). Run the full automated
suite after code changes and all ten live test projects after changes to project
construction or export execution. Re-reading old successful task receipts does
not validate a changed build. Use a local development checkout for these tests.

For the universal fact tree, assigned account analytics and native build version,
read [reference-tree.md](references/reference-tree.md). Present tree as a tree.

Prefer the `Обороты` virtual table for accumulation-register exports when
available and suitable; see references/accumulation-registers.md.

Use `analyze_reference_graph` for outgoing links of any entity and document
sections. Expand selected dotted paths with `expand_paths`. Periodic information
registers are skipped. Analytics keys follow exact metadata targets, never name heuristics.

Use only the `extractor1c` MCP tools. Call `list_skills` and then `get_skill` to read server-published Markdown instructions and references. Never construct SQL, execute arbitrary 1C code, or call the 1C publication directly.

For project work, read [workflow.md](references/workflow.md). For accumulation registers also read [accumulation-registers.md](references/accumulation-registers.md).

For requests to schedule daily or weekly exports, run every N minutes, pause or resume automatic export, follow the scheduling workflow below. Scheduling an existing project does not require creating the project again or changing its rows.

## Транспорт и версия

MCP и EPA_Projects используют 3.2.0; Экстрактор - начиная с 3.16.1.xx. Вызови `list_bases`, выбери `base_id` и передавай его во все операции. В прямом HTTP-режиме базы перечислены в YAML на сервере; в DCC-режиме базы обнаруживаются через DCC, а 1С сама получает команды. DCC-маршрут не обращается к публикации 1С. `segmentation` передаётся массивом группировок с явным `field` и тремя Boolean-ролями; объект с `mode` не используется.

## Creating projects and changing their contents

Ask for the partition choice unless already specified: documents by day or
Ссылка; catalogs by the first two characters of Наименование/Код or Ссылка.
Reference partitioning uses batches of 1000. Read
[partition-segments.md](references/partition-segments.md) before preview.

First follow "Connection selection" in [workflow.md](references/workflow.md): none requires 1C configuration, one is automatic, several require a user choice reused in the conversation.

For a document tabular section partitioned by the header date, read "Document
tabular sections and header dates" in [workflow.md](references/workflow.md).
Verify the saved partition path is `Ссылка.Дата` before initialization and export.

1. Call `readiness`, then use `search_metadata`, `describe_object_fields`, `list_connections`, and `list_projects` for discovery.
2. Present the intended entities and target tables to the user before any write.
3. Call `preview_project` or `preview_project_properties`. Keep the returned `planId`; do not rebuild the payload after preview.
4. After explicit user approval, generate a new UUID `operationId` and call `create_project` or `update_project` with only `planId` and `operationId`.
5. Add related rows one at a time and call `verify_project_summary` after every write.
6. If a write result is uncertain, call `get_operation_status` and verify project state. Never retry with another operation ID until the state is resolved.

Call the setting a **partition segment**, not a grouping. Project creation configures an export but does not start data export.

For scheduling, use `get_project_schedule`, `preview_project_schedule`, then after user approval `set_project_schedule(plan_id, operation_id)` and read back. Read the scheduling section in [workflow.md](references/workflow.md). Enabling can start export of the whole project; show the 1C time context, execution user from Extractor general settings, activation and blockers. A row's `disabled` flag is not schedule activity.

For hourly repetition and screenshots of the native 1C schedule dialog, read 'Hourly repetition and native 1C fields' in [workflow.md](references/workflow.md). Distinguish repetition intervals, pauses after execution, and daily time limits.

## After creating a project

After creation and successful verification, establish the user choices:

1. Initialize the export queue? Yes / no.
2. Run the project now? Yes / no.
3. If running: wait for completion? Yes / no.
4. Schedule the project? Yes / no; if yes, what schedule?

Do not ask again for choices or parameters already supplied. Bundle the unanswered questions in one message. Follow 'After creating a project' in [workflow.md](references/workflow.md) for execution order and schedule details.

Ask about waiting only when a run is selected and the preference is unknown. Yes means poll the task until a terminal result. No means return the acknowledged launch with taskId and available backgroundJobId, without polling until completion. Report started, not successfully completed; status can be requested later. Not waiting does not cancel the launch. Do not repeat an already stated wait/no-wait preference.

## Immediate export

Read 'Run a project' in [workflow.md](references/workflow.md). Use `preview_project_export(project_guid)`, then the authorized `start_project_export(plan_id, operation_id)`, and, if waiting was selected, poll `get_project_export_status(project_guid, task_id)` using the returned task ID; otherwise return launch confirmation and task ID. Show the whole project, HTTP execution user and immediate activation. Existing explicit authorization remains valid when the preview matches it. A completed MCP operation is not a completed export. Never retry an uncertain launch with a new plan or operation ID.

## Queue initialization

For initializing or preparing an export queue, read 'Queue initialization' in [workflow.md](references/workflow.md). Use `preview_project_initialization`, then the authorized `initialize_project_queue`, and poll `get_project_export_status` with its returned task ID. Initialization is a separate action; an export request alone does not authorize it.
