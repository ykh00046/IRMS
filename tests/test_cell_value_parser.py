"""cell_value_parser 단위 테스트 - Excel 셀 값(숫자/수식/혼합텍스트) 분리.

모든 레시피 값의 진입점이며 "마지막 숫자 우선" 규칙을 검증한다(DB/HTTP 불필요).
"""
from src.services.cell_value_parser import _is_number, parse_cell


class TestIsNumber:
    def test_integer(self):
        assert _is_number("10") is True

    def test_float(self):
        assert _is_number("1.5") is True

    def test_negative(self):
        assert _is_number("-3") is True

    def test_text(self):
        assert _is_number("x") is False

    def test_hyphenated_code(self):
        assert _is_number("BYK-199") is False


class TestParseCellEmpty:
    def test_none(self):
        assert parse_cell(None) == (None, None)

    def test_empty_string(self):
        assert parse_cell("") == (None, None)

    def test_whitespace_only(self):
        assert parse_cell("   ") == (None, None)

    def test_dash_placeholder(self):
        assert parse_cell("-") == (None, "-")


class TestParseCellNumeric:
    def test_pure_int(self):
        assert parse_cell("360") == (360.0, None)

    def test_pure_float(self):
        assert parse_cell("12.5") == (12.5, None)

    def test_trimmed(self):
        assert parse_cell("  5  ") == (5.0, None)

    def test_last_number_wins(self):
        assert parse_cell("10 20 30") == (30.0, None)


class TestParseCellFormula:
    def test_formula_preserved_as_text(self):
        assert parse_cell("=A1*2") == (None, "=A1*2")

    def test_formula_with_leading_space(self):
        # 선행 공백은 strip 후 '='로 시작 → 수식
        assert parse_cell("  =SUM(1,2)") == (None, "=SUM(1,2)")


class TestParseCellMixed:
    def test_weight_with_paren_memo(self):
        assert parse_cell("12.50 (HR10)") == (12.5, "(HR10)")

    def test_text_and_paren_and_number(self):
        # 마지막 숫자=360, 괄호메모 보존, 외부 텍스트 보존
        assert parse_cell("APB(17) 360") == (360.0, "APB (17)")

    def test_hyphenated_code_not_split(self):
        # 'BYK-199'는 숫자로 분해되지 않고 텍스트로 보존
        assert parse_cell("BYK-199") == (None, "BYK-199")

    def test_pure_text_returns_text(self):
        assert parse_cell("no number here") == (None, "no number here")
