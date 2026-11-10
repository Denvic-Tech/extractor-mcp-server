# Работа через DCC: метаданные и preview

Выбери базу через list_bases и используй её base_id во всех операциях.
В DCC-режиме используй search_metadata и describe_object_fields вместо полного
каталога/описания. Граф без account_scope строится по сохранённому снимку DCC.

Получи get_metadata_snapshot и сохраняй snapshotId. Передавай его как snapshot_id
для страниц поиска, полей и графа. Страница - часть списка объектов либо полей
одного объекта; nextCursor передаётся следующим cursor. Снимок не менять молча.
Учитывай warnings, objectWarnings, metadataEnriched и неполноту графа.

При использовании preview передавай snapshotId как metadata_snapshot_id в
preview_project или preview_project_template; план сохраняет metadataEvidence.
Preview исполняется в целевой 1С задачей DCC; 1С забирает команду сама.
SQL при capability direct_sql_operations использует прямые существующие операции
без обязательного preview. DCC не меняет тип приёмника: Kafka, AMQP и 1С Шина
по-прежнему требуют план. Не добавляй metadata_snapshot_id в прямой payload,
если фактическая схема инструмента его не принимает.
Прямого запроса MCP к 1С нет. Назначенные субконто с account_scope также требуют
живой задачи; snapshot_id с этим режимом не смешивать.

При тайм-ауте сохраняй operation_id и запрашивай get_operation_status для той же
базы. queued/running - незавершённые состояния. Диагностика в dcc не разрешает
повторять неизвестное изменяющее исполнение новым UUID. Не менять транспорт
из-за недоступности DCC и не использовать старый long polling вместо pending.
