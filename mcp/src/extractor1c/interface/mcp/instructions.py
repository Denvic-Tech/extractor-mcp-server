"""Инструкции сервера для ИИ-клиента. Текст входит в контракт MCP: клиент получает его при подключении.

Каждый раздел — отдельное правило работы. Изменение текста меняет поведение клиентов и
фиксируется снимком в tests/fixtures/tool_contract.json.
"""

AI_SKILL_REQUIREMENTS = (
    "AI requirement after authentication: before project work, you MUST call "
    "list_skills, then call get_skill with the returned skill_id and "
    'document="SKILL.md" for each relevant skill. Read the returned Markdown '
    "and use get_skill to read its task-relevant reference documents, using "
    "document names returned by list_skills. For project operations, read "
    'get_skill(skill_id="extractor1c", document="references/10-mcp-projects.md"). '
    "Do not treat the skill listing or tool descriptions as a substitute for "
    "reading the skill documents. If a required document cannot be read, "
    "report the failure and stop dependent project work."
)

PROJECT_WRITES = """Use search_metadata and describe_object_fields for discovery. For SQL project writes use create_project(definition=...) or update_project(change=...) with a new UUID operation_id; direct mode requires the direct_sql_operations EPA capability and a native SQL receiver check. Updates retain expectedState. Preview is optional for SQL. For Kafka, AMQP and 1C Bus receivers preview_project is required: use its plan_id with operation_id. Do not ask for authorization again when the user already authorized the action. For schedules read get_project_schedule, call preview_project_schedule, show the time context, execution user and activation warning, obtain user approval, then set_project_schedule with only planId and a new UUID operationId and read get_project_schedule again. Enabling a schedule can start data export. A repeated operationId only replays a completed result; an indeterminate operation must be resolved with verify_project_summary (or get_project_schedule for schedules) before any new write. Add related project rows one at a time and inspect the returned server verification after each write; reread only when unavailable. Project creation configures export settings but does not run data export. Never send arbitrary code or SQL."""

EXPORT = """For authorized immediate SQL export call start_project_export(project_guid=..., operation_id=...) directly; do not require a separate preview RPC. For Kafka, AMQP and 1C Bus use preview_project_export and then start_project_export(plan_id=..., operation_id=...). Native validation runs inside the direct SQL operation before launch. Poll get_project_export_status with the returned taskId. get_operation_status tracks the launch request, not export completion. Never retry an uncertain launch with another plan or operation ID: unresolved launches block further starts for that project in this operation store and require administrator reconciliation. Reading status alone does not clear this block. The latest task is not proof of which request started it."""

QUEUE_INITIALIZATION = """Queue initialization is separate from export. When explicitly authorized, initialize a SQL project directly with initialize_project_queue(project_guid=..., operation_id=...). For Kafka, AMQP and 1C Bus use preview_project_initialization and then initialize_project_queue(plan_id=..., operation_id=...). Never provide both a plan and direct input; operation_id is required in either mode. Native initialization handlers may clear or refill queues; existing schedules may consume prepared data. Do not initialize merely because export was requested. Poll get_project_export_status using the returned taskId (taskType initialization); without taskId it still reads latest export. A finished launch means native synchronous fallback ended: inspect its status, not just launchStatus. Uncertain initialization and export operations block both kinds of new launch for the same project."""

QUERY_SOURCES = """For a ready 1C query use existing create/preview with queryText and explicit segmentation. Reuse the object segment-choice dialog. Bind generated queue grouping names to query parameters through queryParameterNames; fixed typed values belong in queryParameters. Prefer virtual-table parameters for register segments and WHERE conditions for catalogs/documents. Each queued segment runs the same query with its own parameters. Query sources require query_sources; never submit executable BSL or rewrite query business logic."""

BASES = """Call list_bases before base-specific work. Pass the same base_id to discovery, preview, commit and status tools. Direct HTTP bases come from the configured YAML bases_file or environment settings; DCC bases are all accessible connectors advertising extractor-1c/2.0 and the mcp command. There is no automatic transport fallback. If DCC waiting expires, query get_operation_status with the same operation_id and base_id; never submit another write to replace an uncertain request."""

DIAGNOSTICS = """For bounded native query/history diagnostics use get_project_export_status with the original task_id and include_diagnostics=true only when the selected EPA advertises query_diagnostics. This reads project history within a task time window; it is incomplete and cannot prove exact task correlation, typed parameters or replay. Keep native timestamp units and log text semantics. Ordinary status omits diagnostics and works with older EPA. Never replace unavailable diagnostics with arbitrary code or query execution."""

SERVER_INSTRUCTIONS = " ".join((
    AI_SKILL_REQUIREMENTS,
    PROJECT_WRITES,
    EXPORT,
    QUEUE_INITIALIZATION,
    QUERY_SOURCES,
    BASES,
    DIAGNOSTICS,
    "For a server-managed project dialogue, use preview_project(definition=..., scenario={...}). "
    "Pass already authorized initialize/export/wait choices. Return only offered choice IDs with scenarioId and revision. "
    "Forward nextCall with its exact operation_id and base_id within the user's authorized scope, then read "
    "get_project_plan(plan_id=scenarioId). The scenario persists choices and operation identities across restarts; "
    "it does not perform business writes. Native helper owns SQL types and receiver creation; never reconstruct its DDL.",
    "Prefer compact decisions: metadata segmentChoices and graph choices, preview/plan decision, saved-write verification, receiver verification and task decision. Use details=true only when needed. Reuse the short stateToken returned by verify_project_summary or a saved write as expectedState; never reconstruct a native snapshot. Use bounded wait_seconds to wait on the same task without restarting it.",
))
