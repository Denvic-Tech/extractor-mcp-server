# Universal reference tree and 1C methods build version

Use `analyze_reference_graph` for accumulation/accounting registers, documents
and sections, catalogs, charts of accounts, characteristic plans, nonperiodic
information registers and enums. Show `tree` with each discovery path. Enums
are terminal export candidates. Preserve polymorphic branches, repeated paths
and cycle/identity references. Skip periodic information registers.
Virtual register tables use base-register metadata for analysis; verify actual
export columns separately through preview. Never equate a metadata link with
a verified join or observed usage in movements.

For an accounting register or chart root pass
`account_scope={"codes":["51"],"includeSubaccounts":true,"maxAccounts":200,"maxKinds":1000}`.
For another root a single linked chart must be discoverable unambiguously.
The tree reads chart -> account/subaccounts by hierarchy -> assigned subconto
kind -> characteristic plan -> the individual kind's allowed value types.
It replaces broad standard subconto metadata unions for scoped registers.
Primitive types remain visible but are not entity export candidates.
Use accountAnalytics.exportCandidates as candidates to agree with the user;
do not automatically create projects or treat allowed types as observed values.

Read-only under the current 1C user's permissions. A missing or inaccessible
account is reported explicitly. Limits: 20 codes, maxAccounts 1-1000,
maxKinds 1-5000, 2000 value-type descriptions in the native response.
max_nodes also caps the occurrence tree; max_depth caps metadata hops and account
ancestry separately from kind/plan/type semantic levels. Check complete,
truncated and warnings before claiming completeness. Expand candidates' outgoing
links with another graph analysis and selected expand_paths.

For journal exports, native sources Хозрасчетный.Субконто (normalized rows)
and Хозрасчетный.ДвиженияССубконто (debit/credit columns) were verified in ERP.
Always preview the actual database. Preserve GUID/type, movement side and line
number. Joining normalized subconto multiplies journal rows and their amounts.

Read readiness.oneCMethodsVersion and oneCCapabilities from the selected deployed base. MCP and EPA_Projects version is 3.2.0, Extractor versions starting with 3.16.1.xx are supported. account_scope requires account_subconto_settings. Never substitute a local version for an unavailable deployed version. Segmentation uses an array; read [partition-segments.md](partition-segments.md). Platform 8.3.5 and compatibility mode 8.2 require separate runtime validation.
