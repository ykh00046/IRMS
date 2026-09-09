"""자재 LOT 저장 시 앞뒤 따옴표·공백 제거(2026-09-09 사고 재발 방지)."""
from src.services.blend_service import normalize_material_lot


def test_strips_excel_quote_and_spaces():
    assert normalize_material_lot("'0029227498") == "0029227498"
    assert normalize_material_lot(" 03136002 ") == "03136002"
    assert normalize_material_lot("’DIVRA-OD’") == "DIVRA-OD"
    assert normalize_material_lot('"5K74"') == "5K74"


def test_keeps_inner_characters_and_empty_is_none():
    assert normalize_material_lot("RM-26'03") == "RM-26'03"
    assert normalize_material_lot("") is None
    assert normalize_material_lot(None) is None
    assert normalize_material_lot("''") is None
