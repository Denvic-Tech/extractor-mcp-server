# Accumulation registers

Prefer `РегистрНакопления.<Name>.Обороты` after confirming availability and
fields through MCP. Analyze links on the base register, but select export
fields from the virtual table. Verify its grain and periodicity in preview:
the `День` partition segment does not itself set turnover periodicity.
Use another source for explicitly requested raw movements, balances or other
semantics. Do not silently replace an unavailable virtual table.

Call `analyze_reference_graph(object=...)` first. It returns nodes, edges, exact targets and dotted paths. Nonperiodic information registers expand automatically; periodic registers are skipped with reason `periodic_register`, including explicitly requested paths.

Use `expand_paths` to expand selected catalogs, documents and characteristic plans. Analytics keys can be ordinary catalog references: request `АналитикаУчетаНоменклатуры.Номенклатура` to discover item units and kinds. Never infer an inverse register join from a key's name. Preserve polymorphic branches using `steps`; verify join keys before project writes. Check `complete`, `truncated`, `warnings` and `unresolvedExpandPaths` before claiming completeness.

For any document, `include_tabular_sections=true` discovers all sections returned by the updated API and reads their references. Each section has its own grain and a one-to-many owner relationship. An old API without discovered sections returns `tabular_sections_unconfirmed`.

Show both direct and indirect related catalogs/documents and ask which ones should become separate rows in the same project. Default the accumulation-register partition segment to `День`.

Ask for the partition choice of each related catalog/document as described in [partition-segments.md](partition-segments.md), reusing an explicit shared user choice. Set `maxItemsPerBatch` from the segment semantics: use `0` for period segments (`День`, `Месяц`) and string-prefix segments (`Первые две буквы`). Use `1000` for a chosen `По значению` segment on the confirmed `Ссылка` field or reference key. Do not infer the mode only from the object kind or absence of periodicity.

Create the first row only after the entity list is approved. Add every remaining row separately, preserving identical relationship-key column names, and verify after each addition.
