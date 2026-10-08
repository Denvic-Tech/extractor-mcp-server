# Hosted project workflow

API 3.2.0: сначала `list_bases`, выбери `base_id` и передавай его во все вызовы. Несколько HTTP-баз настраиваются YAML; DCC-базы обнаруживаются через DCC. Новую многоролевую сегментацию передавай массивом; описание - [partition-segments.md](partition-segments.md). Одно-сегментные примеры ниже остаются допустимым отдельным вариантом и не смешиваются с массивом.


Before preview of catalog or document rows, ask for the partition choice
following [partition-segments.md](partition-segments.md). Do not automatically
choose document day partitioning or catalog name prefixes.

## Connection selection

Read `list_connections` before project work. It lists live connections of all
types from 1C without secrets. Never treat an example connection name as a default.
If empty, ask the user to configure a connection in Extractor 1C and refresh
after confirmation. If exactly one, use it automatically. If several, show
names/types and ask which to use for subsequent project development; reuse an
explicit prior answer without asking again.

Keep the choice in the current conversation and pass its `connectionName` on
subsequent projects. Recheck the live list before preview. If the choice was
removed, ask again; duplicate names require renaming in 1C. Do not share this
choice between users or infobases. There is no server-global default; a fresh
conversation without saved context must select again.

MCP fills an omitted/empty connectionName only when one connection exists.
Otherwise preview returns approved=false, planId=null and connectionSelection
with the required user action. Explicit names are validated against the live
list and bound into all plan rows; commit does not reselect. readiness.ready
reports transport readiness, while connectionSelection reports selection state.
Existing projects retain their verified connectionName during row updates.

There is no ClickHouse type filter. Field types and target capabilities come
from the native Extractor driver and must be verified in preview. The legacy
column-alteration operation still uses ClickHouse SQL; for other drivers it
rejects before DDL and asks for column configuration in 1C. This is an operation
limitation, not a restriction on choosing or creating projects.


## Document tabular sections and header dates

Use `Документ.<DocumentName>.<TabularSectionName>` as the source. Read its fields
with `describe_object_fields`, then inspect `<source>.Ссылка` to confirm the
header's `Дата` field has date type. The document-date partition path is
`Ссылка.Дата`, not `Дата` or a section-row date such as `ДатаОтгрузки`.
Leave `segmentField` empty for date segments `День` and `Месяц`.

Verified example: `Документ.ЗаказКлиента.Товары`, `segment="День"`,
`includeGuids=true`, `includeMetadataTypes=false`, all section fields requested
(no selectedFields restriction). Preview returned 53 mappings, including
`СсылкаДата`, `Параметр.ДатаДень`, and `СсылкаГуид`. These mapping names do not
replace the partition path. Preserve the segment parameter and required GUID
calculations when selecting fields; discover actual mappings for other objects.

Before initialization or export, verify `verify_project_summary.rows[].grouping`:
`enabled=true`, `field="ДатаДень"`, `path="Ссылка.Дата"`, `function="День"` for this
example. Fix an incorrect path before proceeding. A tabular-section discovery
error may require updating the API extension; do not substitute the header.

Live validation on 2026-09-25: project `mcp25_ЗаказКлиента_Товары`, table
`mcp25_customer_order_items`, correct saved path, initialization `succeeded`,
then export `started`. The user chose not to wait, so export completion was
not checked. No schedule was configured, per user choice. For initialization
followed by a no-wait export, wait for initialization success first, then
launch export and return task identifiers without polling export completion.

Prefer compact tools so hosted agents do not receive entire metadata catalogs or state tokens. Use cursors until `nextCursor` is null.

The preview is a server-side immutable plan with a short lifetime. A write accepts the plan ID, not the original definition. A completed `operationId` can be repeated safely and returns the stored result. An in-progress or indeterminate operation must be reconciled with `get_operation_status` and `verify_project_summary`.

Before building `selectedFields`, call `describe_object_fields`. Intersect the minimal candidates for the object kind with the actual metadata field names. Include only confirmed fields and generated GUID or segment fields whose source exists. Do not use failed previews to discover missing attributes.

For catalogs, default to `Ссылка` as an entity-named GUID column, `Наименование`, and `Код`; include each field only when present. Include `Группа` when present. Include ИНН, КПП and ОГРН for counterparties, and `Группа` and `ЕдиницаИзмерения` for nomenclature. Ask for the partition choice as described in partition-segments.md.

For documents, default to the entity GUID, `Номер`, `Дата`, `Проведен`, `ПометкаУдаления`, and `Представление`; include only fields confirmed by metadata. Ask for the partition choice and inspect metadata first. For a chosen day segment prefer date-typed `Дата`, otherwise date-typed `Период`; use `maxItemsPerBatch: 0`. For a chosen reference segment use `По значению`, `segmentField: Ссылка` and `maxItemsPerBatch: 1000`. With explicit selectedFields preserve the actual generated segment parameter. Do not export every field automatically.

Use the same Latin GUID column name on both sides of every relationship. Let the Extractor driver choose the database-native UUID type.

## After creating a project

Once all agreed project rows have been created and `verify_project_summary` succeeds, establish the user choices. Ask all unanswered questions together:

1. Initialize the export queue? Yes / no.
2. Run the project now? Yes / no.
3. If running: wait for completion? Yes / no.
4. Schedule the project? Yes / no. If yes, what schedule?

Reuse explicit choices and parameters already supplied in the current task. No answer is not authorization. Scheduling alone does not authorize manual initialization or an immediate manual run. A no to scheduling means skip configuration, not disable an existing job.

Ask only for missing parameters of the chosen schedule: daily time; weekly days and time; or an interval and daily window required by the API. Read and present the 1C time context. Do not separately ask for the execution user; use API-provided settings.

Apply selected actions in order: initialize, wait for success, run manually, then configure scheduling. Wait for export completion only if selected; otherwise an acknowledged launch is sufficient. If a later action is currently blocked by the API, report that instead of silently waiting. Skip unselected actions. Each selected action uses its own preview and planId/operationId, followed by launch confirmation or task-result verification according to the waiting choice, or schedule readback. Stop dependent actions on an error or uncertain outcome and explain why. Enabling a schedule may itself start export; show this in preview. Do not repeat approval requests when the preview matches the user's existing authorization.

Ask about waiting only when a run is selected and the preference is unknown. Yes means poll the task until a terminal result. No means return the acknowledged launch with taskId and available backgroundJobId, without polling until completion. Report started, not successfully completed; status can be requested later. Not waiting does not cancel the launch. Do not repeat an already stated wait/no-wait preference.

## Scheduling an existing project

Check `readiness` and verify that all three scheduling tools exist in the connected MCP tool list. If absent, report that the MCP server and 1C API extension need updating; do not substitute arbitrary BSL, direct HTTP or `update_project`. Ask only for missing schedule parameters: time, weekdays, or interval and daily window as required by the chosen mode. Do not ask for an execution user; Extractor general settings already define it.

Resolve the project name with `list_projects`, then its UUID from `verify_project.projectRef`, then call `get_project_schedule(project_guid)`. Call `preview_project_schedule(change)` with `projectGuid`, boolean `enabled` and optional `schedule`:

- Daily: `{"kind":"daily","time":"08:30:00"}`.
- Weekly: `{"kind":"weekly","time":"08:30:00","weekdays":[1,3,5]}`; Monday is 1, Sunday is 7.
- Interval: `{"kind":"interval","intervalMinutes":15,"startTime":"09:00:00","endTime":"18:00:00"}`; 1-1440 minutes, start strictly before end within one day.

Times use `HH:MM:SS` in the reported 1C time context, not the client's assumed timezone. Omit `schedule` or use null to preserve settings when pausing (`enabled:false`) or resuming (`enabled:true`). A new job requires an explicit schedule. The execution user comes from Extractor general settings; there is no API parameter for choosing a different user.

Present current and desired state, time context, execution user, activation and blockers. Enabling can immediately start export; enabled does not prove execution is permitted or completed. The schedule covers all project rows.

After user approval, call `set_project_schedule` with only `plan_id` and a new UUID `operation_id`, then read actual state again. The immutable plan contains `expectedScheduleState`; never replace it. `approved:true` means the preview is valid, not that the user approved execution.

After a network error, use `get_operation_status` and `get_project_schedule`; do not bypass an unresolved operation with a new ID. A stale link to a missing job requires native repair; the API does not silently recreate it. Read and preview need no license; schedule writes use the native license and support gate. Update both the 1C API extension and MCP server. Avoid concurrent edits by the native UI or administrator; the native scheduler does not provide atomic compare-and-set with API writes.

### MCP call arguments

Replace placeholders with actual returned values; never submit them literally.

1. `verify_project`: `{"project_name":"<existing project name>"}`; read `projectRef`.
2. `get_project_schedule`: `{"project_guid":"<projectRef>"}`.
3. `preview_project_schedule`:

```json
{
  "change": {
    "projectGuid": "<projectRef>",
    "enabled": true,
    "schedule": {"kind": "daily", "time": "08:30:00"}
  }
}
```

4. Present the preview. After user approval, call `set_project_schedule`: `{"plan_id":"<returned planId>","operation_id":"<new UUID>"}`.
5. Read `get_project_schedule` again with the same project UUID. Report actual schedule, `enabled`, `jobId` and blockers; do not claim export has completed.

For pause, use `{"change":{"projectGuid":"<projectRef>","enabled":false,"schedule":null}}` at step 3. Resume uses the same request with `enabled:true`. Changing times uses a new explicit `schedule`. Keep preview, approval and readback for all these operations.

### Hourly repetition and native 1C fields

“Every day, once an hour” maps to two native settings:

- «Общие» -> «Повторять каждые»: 1 day (`ПериодПовтораДней = 1`).
- «Дневное» -> «Повторять через»: 3600 seconds (`ПериодПовтораВТечениеДня = 3600`).

Use MCP `kind="interval"`, `intervalMinutes=60`; `daily` means one run per day. «Повторять с паузой» is a pause after the previous execution completes, not a repetition interval. The user's native example has zero pause and no end time.

The start time anchors runs within the day. A 3600-second interval alone does not imply runs exactly on the hour. Preserve an explicitly requested start time; do not turn a time shown in an illustration into a default for other projects.

An unset native end time is different from an explicit `23:59:59`. The current interval API requires both `startTime` and `endTime`, with start strictly before end; it cannot yet express an interval without an end-time limit. Do not send unsupported empty values or silently add a limit. Explain the difference and agree on a window if none was specified. `00:00:00-23:59:59` is an explicitly selectable window, not an exact copy of unset native fields. Recheck this limitation against the actual contract when the API changes.

After applying, read back activity, interval, start/end times and job ID. `PROJECT_ALREADY_RUNNING` means the project is currently executing; it proves neither successful export completion nor that the new schedule caused that run.

## Run a project

Check that `preview_project_export`, `start_project_export` and `get_project_export_status` are available. If missing, update MCP and our `ExtractorProjectsAPI` extension; the Extractor core requires no changes. Never fall back to the old native HTTP launch/status handlers, arbitrary BSL or direct publication calls.

1. Find the project with `list_projects`, then read `verify_project(project_name).projectRef`.
2. Read `get_project_export_status(project_guid)` for an overview. Without a task ID it selects the latest export, prioritizing a running record.
3. Call `preview_project_export(project_guid)`. Show `summary.projectName`, `current`, `executionUser`, `activation` and `blockers`. The whole project runs immediately with its saved rows, handlers and queue. The schedule is unchanged and the queue is not reset. The execution user is the current HTTP user, not the schedule account.
4. If `approved:true` and the user authorized this run, call `start_project_export(plan_id, operation_id)` using the returned plan and a new UUID. Do not ask again when existing explicit authorization covers the preview.
5. Save `result.taskId` and `result.backgroundJobId`. If waiting is selected, poll `get_project_export_status(project_guid, task_id)` with that task ID; otherwise return the launch confirmation and IDs without further polling. Space out polls and report progress during long runs.

`launchStatus: started` confirms task/job creation, not export success. `already_running` or `initializing` returns an existing task; no new export started. `rejected` is a refusal before launch and needs a fresh preview after the cause is resolved. Execution statuses `succeeded`, `failed`, `cancelled` are terminal; `pending` and `running` are not. `unknown` and `not_found` never prove success. Native cleanup may remove tasks older than three days. Start/end timestamps are UTC milliseconds. Status reads never delete job messages.

`get_operation_status` tracks the MCP request. Its `completed` may even contain a rejected launch; inspect the result. Export task IDs, background job IDs and scheduled job IDs are distinct.

On a lost response or unknown outcome, read operation status and the known task. Do not start again with a new plan or ID. Unresolved operations block further launches for the same project in the persistent MCP operation store; reading status does not release this block. The latest task alone cannot identify a lost request. Administrator reconciliation is required and there is no public force-reset command. Never delete or replace the operation store to bypass this protection.

## Queue initialization

The native HTTP export launch has no separate initialization mode. Use `preview_project_initialization(project_guid)` and `initialize_project_queue(plan_id, operation_id)`; our API invokes the unchanged native `ИнициализироватьПроект`.

1. Resolve the existing project's `projectRef` with `verify_project`.
2. Preview initialization. Show `queueRows`, `eligibleRowCount`, `initializationHandlerCount`, `current`, `currentInitialization`, execution user, blockers and activation.
3. With `approved:true` and user authorization, apply the returned plan using a new operation UUID. Existing authorization remains valid when the preview matches it.
4. Poll `get_project_export_status(project_guid, task_id)` with the returned task ID. Its taskType is initialization; omitting the ID still reads the latest export.

Only enabled rows with queue usage run their enabled native primary-initialization handlers. Those handlers may clear or refill queues and execute stored logic. The API does not start export afterwards; an existing schedule may consume the prepared queue. No eligible rows or handlers means a blocked plan. The current HTTP user needs project modification rights or the native initialization role `Экс_ИнициализацияОчередиПроектов`.

`started` requires task polling. The native implementation can fall back to synchronous execution: `finished` carries a separate `status` (succeeded, failed or cancelled), not unconditional success. already_running, initializing and rejected do not prove a new initialization started. Successful handlers do not guarantee a nonempty queue.

Uncertain initialization and export outcomes block both launch types for the same project, including new plan and operation IDs. Read known task and operation status and reconcile; never delete the store or retry under a new ID. Completed request status alone does not mean initialization succeeded.
