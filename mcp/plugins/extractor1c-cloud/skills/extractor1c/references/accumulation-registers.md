# Accumulation registers

Prefer `РегистрНакопления.<Name>.Обороты` after confirming availability and
fields through MCP. Analyze links on the base register, but select export
fields from the virtual table. Verify its grain and periodicity in saved settings or an optional preview:
the `День` partition segment does not itself set turnover periodicity.
Default turnover periodicity to `Регистратор`, preserving another explicit
choice. Ask for both partition roles, offering **Day regular / Month
initialization**. Do not silently choose them or replace an agreed simple mode.
Before project preparation, ask whether to show reference presentations: Yes or No,
offering No. Reuse an explicit answer. For Yes, enable their export. For No,
mark reference presentations with the native “Do not export” flag
(`НеСоздаватьПоле`), retaining mapping rows, GUIDs and compound-reference types.
Removing mapping rows through `selectedFields` is not the same operation; use
the native wizard settings and never invent an unsupported MCP parameter.
Use the native wizard for source construction, calculations, segmentation and
SQL mapping wherever supported.

The shared native wizard must also build all segmentation-role fields and
their SKD selection. Fix missing fields in that shared mechanism; do not add
an MCP-only algorithm or replace a missing date in the export buffer. Verify
both roles in the native final query.

Export identifiers of all reference fields
using the native `УникальныйИдентификатор` function, retaining compound types.
Disable “Экранировать” for these GUID mappings, preserving native types and
lengths. Confirm their function and source field rather than relying on names.
For every connection, this rule applies to any chosen segment field whose
actual metadata/native-preview type is a reference; `Ссылка` and `Регистратор`
are examples only. Keep its original typed parameter in the 1C query filter:
never substitute a GUID expression or GUID string. After obtaining the value,
the native E1C `УникальныйИдентификатор` function/module handles its type and
calculates the GUID used by the corresponding SQL segment-role field.
Exporting the reference itself is optional; presentation visibility does not
change the GUID role. Read the exact source/parameter/calculation/SQL-role
binding from saved `verify_project` settings or an optional native preview/get_project_plan; never invent `Параметр.<Field>`.
Keep the actual native GUID calculation in explicit `selectedFields`, preserve
native GUID types/lengths and leave "Экранировать" unchecked. Do not apply this
rule to dates, strings, numbers, booleans or other nonreference types. After a direct SQL write, verify the actual role field
with `verify_project`; when preview is used, also check it before writing.
This contract does not replace checking the running base.
Use another source for explicitly requested raw movements, balances or other
semantics. Do not silently replace an unavailable virtual table.

Call `analyze_reference_graph(object=...)` first. It returns nodes, edges, exact targets and dotted paths. Nonperiodic information registers expand automatically; periodic registers are skipped with reason `periodic_register`, including explicitly requested paths.

Use `expand_paths` to expand selected catalogs, documents and characteristic plans. Analytics keys can be ordinary catalog references: request `АналитикаУчетаНоменклатуры.Номенклатура` to discover item units and kinds. Never infer an inverse register join from a key's name. Preserve polymorphic branches using `steps`; verify join keys before project writes. Check `complete`, `truncated`, `warnings` and `unresolvedExpandPaths` before claiming completeness.

For any document, `include_tabular_sections=true` discovers all sections returned by the updated API and reads their references. Each section has its own grain and a one-to-many owner relationship. An old API without discovered sections returns `tabular_sections_unconfirmed`.

Show both direct and indirect related catalogs/documents and ask which ones should become separate rows in the same project. Agree on Day regular / Month initialization unless another scheme is already selected.

Ask for the partition choice of each related catalog/document as described in [partition-segments.md](partition-segments.md), reusing an explicit shared user choice. Set `maxItemsPerBatch` from the segment semantics: use `0` for period segments (`День`, `Месяц`) and string-prefix segments (`Первые две буквы`). Use `1000` for a chosen `По значению` segment on the confirmed `Ссылка` field or reference key. Do not infer the mode only from the object kind or absence of periodicity.

Create the first row only after the entity list is approved. Add every remaining row separately, preserving identical relationship-key column names, and verify after each addition.
