"""recipe_helpers의 순수 함수 단위 테스트 (DB 불필요한 표시값 조합만)."""
from src.services.recipe_helpers import format_display_value


class TestFormatDisplayValue:
    def test_weight_and_text(self):
        assert format_display_value(12.5, "HR10") == "12.5 (HR10)"

    def test_weight_only_empty_text(self):
        assert format_display_value(12.5, "") == "12.5"

    def test_weight_only_none_text(self):
        assert format_display_value(12.5, None) == "12.5"

    def test_text_only(self):
        assert format_display_value(None, "memo") == "memo"

    def test_both_none(self):
        assert format_display_value(None, None) == ""
