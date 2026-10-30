"""Real native previews preserve normalized and wide subconto links."""
import json
from pathlib import Path

FIXTURE = json.loads((Path(__file__).parent / "fixtures/subledger_preview_erp.json").read_text(encoding="utf-8"))

def test_normalized_subconto_has_movement_join_and_polymorphic_value():
    fields = {f["source"]: f for f in FIXTURE["normalized"]["items"]}
    assert {"Период", "Регистратор", "НомерСтроки", "ВидДвижения", "Вид", "Значение", "ЗначениеТип", "РегистраторТип"} <= fields.keys()
    for name in ("РегистраторГуид", "ВидГуид", "ЗначениеГуид"):
        assert fields[name]["type"] == "UUID"

def test_wide_subconto_preserves_all_six_reference_branches():
    fields = {f["source"]: f for f in FIXTURE["wide"]["items"]}
    for side in ("Дт", "Кт"):
        for index in (1, 2, 3):
            name = f"Субконто{side}{index}"
            assert {name, name + "Тип", name + "Гуид", "Вид" + name + "Гуид"} <= fields.keys()
            assert fields[name + "Гуид"]["type"] == "UUID"
