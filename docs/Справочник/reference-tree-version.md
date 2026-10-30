# Дерево связей: MCP 3.3.3 / EPA 3.3.1

`analyze_reference_graph` возвращает дерево подтверждённых ссылок метаданных. При `account_scope` назначенные виды субконто читаются в 1С; через DCC это выполняется задачей со структурированным результатом. Типы метаданных не доказывают использование значений в движениях.

Подробности: [описание дерева](../../mcp/skills/extractor1c/references/17-reference-tree.md). MCP: 3.3.7; ожидаемый контракт EPA_Projects: 3.3.5; Экстрактор: начиная с 3.16.1.34. Проверяйте отдельно readiness.mcpVersion, expectedOneCMethodsVersion и фактическую oneCMethodsVersion. [Массив сегментации](project-segments.md) одинаков для HTTP и DCC.
