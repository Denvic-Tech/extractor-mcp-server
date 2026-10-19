# Сегментация API 3.2.2

Поддерживается Экстрактор 1С начиная с 3.16.1.xx. `segmentation` - массив группировок, а не объект с `mode`. Каждая группировка содержит обязательные `field`, `segment`, `regular`, `extended`, `initialization`.

```json
{
  "segmentation": [
    {"field":"Регистратор","segment":"По значению","regular":true,"extended":false,"initialization":false},
    {"field":"Период","segment":"День","regular":false,"extended":true,"initialization":false},
    {"field":"Период","segment":"Месяц","regular":false,"extended":false,"initialization":true}
  ]
}
```

`field` - непустой подтверждённый путь поля; для табличной части документа дата шапки задаётся явно как `Ссылка.Дата`. `segment`: `По значению`, `Первая буква`, `Первые две буквы`, `Произвольная подстрока`, `Час`, `День`, `Неделя`, `Месяц`, `Квартал`, `Полугодие`, `Год`. Проверяй соответствие функции подтверждённому типу поля и сохранённым настройкам; preview используй при необходимости. Дополнительные настройки функции, которые не представлены контрактом, через этот запрос не задаются.

Три признака ролей имеют тип Boolean; у каждого элемента хотя бы один равен `true`. Одна группировка может участвовать в нескольких ролях. Массив содержит до 500 элементов; пустой массив явно не задаёт группировки. Не смешивай массив с `segment`/`segmentField`. Простой одно-сегментный запрос остаётся отдельным вариантом; новые многоролевые запросы формируй массивом. Объект `segmentation` с `mode` отклоняется.

Если пользователь ещё не выбрал сегмент партицирования, согласуй его до подготовки проекта: документ - день или ссылка; справочник - префикс наименования/кода или ссылка. Для ссылки используй `По значению`, поле `Ссылка` и `maxItemsPerBatch: 1000`; это размер упаковки, а не предел всей выгрузки. Для дня документа проверь `Дата`/`Период`; для табличной части - `Ссылка.Дата`. Поля с одинаковыми путём и функцией могут участвовать в нескольких ролях через один элемент с несколькими флагами.

Хранение в текущем Экстракторе использует `ПараметрРегулярный`, `ПараметрРасширенный`, `ПараметрИнициализации`. Развилки хранения для предыдущих выпусков Экстрактора не используются. Роли группировки и галки сопоставления полей - разные представления: сверяй сохранённый источник и маппинг, не выводи роль из одной галки.

`selectedFields` должен сохранить фактические поля всех выбранных ролей, параметры сегмента и нужные GUID-вычисления из preview/get_project_plan. Для SQL при подтверждённом `direct_sql_operations` используй прямую запись; в режиме с preview сохрани точный `planId`. После записи выполни `verify_project_summary`. Не заменяй вручную настроенные роли простым запросом без согласованного изменения. Создание настроек не инициализирует очередь и не запускает выгрузку. При неопределённом результате сначала проверь операцию и состояние проекта; не повторяй запись с новым `operationId` вслепую.

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

## Mixed reference and primitive segment keys

Actual field types may allow reference classes and string/number/date/boolean
values together, for example dependent characteristic values or an operations
journal. A GUID alone is insufficient: native GUID is undefined for primitive
values, which can collapse distinct SQL segments. Require the shared native
role vector: GUID, native `ПолноеИмяМетаданных`, and applicable native
`ЕслиТипСтрока` / `ЕслиТипЧисло` / `ЕслиТипДата` / `ЕслиТипБулево` calculations,
selected from actual metadata types. Keep the original typed parameter in the
1C query and preserve the existing pure-primitive path. Before writing, confirm
actual support and every role part through saved native settings or an optional preview/get_project_plan;
explicit `selectedFields` must retain all actual parts. Never guess names or
add a private UUID converter. Verify the same native functions in queue and
buffer and repeated native predicate construction; documentation alone is not
runtime acceptance. Compound-segmentation regression must include actual PVH
dependent values and journal metadata, multiple reference classes plus allowed
primitives, zero, false and UUID-like strings.

For a standalone enumeration without segmentation, use `segmentation: []` and
confirm saved native settings or an optional preview; legacy `segment: ""` is not an enumeration equivalent.
Do not invent segmentation just to satisfy the old validator.
