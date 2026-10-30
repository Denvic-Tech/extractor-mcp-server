# Native project-row settings

Before changing queue limits, batching, concurrency, buffer/block sizes,
block deletion, advanced data checking or parameter-expansion thresholds,
read the server skill with `get_skill(skill_id="extractor1c",
document="references/24-row-settings.md")`.

Require readiness capability `row_settings`; methods version 3.3.5 alone is
insufficient. Read actual values from the selected row's `rowSettings.saved`.
For SQL with confirmed `direct_sql_operations` and direct input schema, use
`update_project(change=..., operation_id=...)` without preview; otherwise use the
corresponding plan workflow. Keep fresh `expectedState`, exact `currentTableName`
and a nonempty `rowSettings` patch. Preserve omitted values;
zero and false are explicit values, null is invalid. Verify the saved result.
Changing these settings does not initialize queues, run exports or perform DDL.

The keys are `maxQueueRecords`, `queueBatchSize`, `maxThreads`,
`threadWaitTimeout`, `bufferSize`, `blockSize`, `deleteInBlocks`,
`deleteBlockSize`, `advancedDataCheck` and `parameterExpansionThreshold`.
Native defaults differ by field; do not infer their meaning from zero.
`queueBatchSize` and legacy `maxItemsPerBatch` refer to the same native field;
conflicting explicit values are invalid. Reuse native wizard/property methods,
not separate execution algorithms or arbitrary code.
