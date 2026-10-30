"""Real ERP preview invariants for the two journal examples."""
import json
from pathlib import Path
import pytest
from extractor1c.projects_api import ProjectDefinition

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((ROOT / "tests/fixtures/ledger_preview_erp.json").read_text(encoding="utf-8"))

@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e["kind"])
def test_journal_preview_preserves_grain_accounts_and_partition(example):
    ProjectDefinition.model_validate(example["definition"])
    fields = {f["source"]: f for f in example["fields"]["items"]}
    assert {"Период", "Регистратор", "НомерСтроки", "Активность", "СчетДт",
            "СчетКт", "Сумма", "Параметр.ПериодДень", "РегистраторТип"} <= fields.keys()
    for name in ("РегистраторГуид", "СчетДтГуид", "СчетКтГуид"):
        assert fields[name]["type"] == "UUID"
    settings = example["summary"]["rows"][0]["sourceSettings"]
    assert settings["maxItemsPerBatch"] == 0
    assert any(g["enabled"] and g["path"] == "Период" and g["function"] == "День"
               for g in settings["grouping"])
    if example["kind"] == "regulated":
        assert {"СуммаНУДт", "СуммаНУКт", "СуммаПРДт", "СуммаВРКт"} <= fields.keys()
    else:
        assert {"СуммаПредставления", "ИдентификаторФинЗаписи", "ТипПроводки"} <= fields.keys()
