# Receiver name preflight

Before create_project, validate all final table and column names after native
transliteration, including generated calculations and segment parameters.
Use the actual receiver/driver limits and units: PostgreSQL commonly 63 bytes
in the server encoding, MySQL 64 characters, SQL Server 128 characters.
Check ClickHouse and Kafka using their own rules. Characters are not bytes.

For an overlong name, use the optional `columnNames` mapping in the existing
create_project definition or preview_project, after confirming `column_names`
in the selected EPA's readiness. Keys are exact Extractor mapping source names;
values are explicit Latin receiver identifiers. Example:

```json
{"columnNames":{"ЗаполнениеДоступностиДляГрафикаПроизводстваКоличествоИнтервалов":"grafikProizvodstvaKolichestvoIntervalov"}}
```

Keep guidColumnNames restricted to GUID calculations. Do not map a source in
both dictionaries. Check collisions with every final native name, not just the
overrides. Preserve fields, types, GUID calculations, noExport/noEscape and all
segment roles. Do not drop fields or truncate values to meet an identifier limit.
The driver still validates the final names before any write. Existing receiver
columns require saved-project fieldChanges; creation does not rename them.
Without the capability, update MCP/EPA before sending this parameter. Verify
saved mapping and receiver schema after creation. Reconcile an indeterminate
operation; never replace it with a fresh operation UUID.
