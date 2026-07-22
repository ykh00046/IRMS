"""BUG-2 회귀: 숫자형 사번 셀 정규화 + 조회 측 정규화.

ERP '사번' 열이 숫자형으로 내보내지면 openpyxl이 float(171013.0)로 읽어
로그인 사번 문자열 '171013'과 어긋난다. 파싱 측(_cell_str)과 조회 측
(employee_exists_in_any_month 등)을 normalize_emp_id 로 맞춘 동작을 고정한다.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import attendance_excel as ex


def _make_raw_row(emp_id_cell: object) -> list[object]:
    row: list[object] = [None] * 34
    row[ex.COL_DATE] = "2026-04-24"
    row[ex.COL_WEEKDAY] = "금"
    row[ex.COL_DAY_TYPE] = "평일"
    row[ex.COL_EMP_ID] = emp_id_cell
    row[ex.COL_GENDER] = "남"
    row[ex.COL_FACTORY] = "A관"
    row[ex.COL_NAME] = "김민호"
    row[ex.COL_JOB_TYPE] = "생산직"
    row[ex.COL_DEPARTMENT] = "자재생산팀"
    row[ex.COL_SHIFT_GROUP] = "정상"
    row[ex.COL_SHIFT_TIME] = "주간"
    row[ex.COL_CHECK_IN] = "09:00"
    row[ex.COL_CHECK_OUT] = "18:00"
    row[ex.COL_NEXT_DAY] = 0
    row[ex.COL_NOTE] = ""
    return row


class NormalizeEmpIdTests(unittest.TestCase):
    def test_int_like_float_becomes_int_string(self) -> None:
        self.assertEqual(ex.normalize_emp_id(171013.0), "171013")

    def test_int_becomes_string(self) -> None:
        self.assertEqual(ex.normalize_emp_id(171013), "171013")

    def test_float_string_form_normalized(self) -> None:
        self.assertEqual(ex.normalize_emp_id("171013.0"), "171013")

    def test_plain_string_stripped_only(self) -> None:
        self.assertEqual(ex.normalize_emp_id("  171013 "), "171013")

    def test_genuine_string_unchanged(self) -> None:
        # 문자형 사번(앞자리 0 포함)은 손상 없이 보존.
        self.assertEqual(ex.normalize_emp_id("017013"), "017013")
        self.assertEqual(ex.normalize_emp_id("A1023"), "A1023")

    def test_both_forms_match(self) -> None:
        self.assertEqual(ex.normalize_emp_id(171013.0), ex.normalize_emp_id("171013"))

    def test_none_and_empty(self) -> None:
        self.assertEqual(ex.normalize_emp_id(None), "")
        self.assertEqual(ex.normalize_emp_id("   "), "")


class CellStrEmpIdTests(unittest.TestCase):
    def test_numeric_emp_cell_parsed_as_int_string(self) -> None:
        rec = ex._row_to_record(tuple(_make_raw_row(171013.0)))
        self.assertEqual(rec["emp_id"], "171013")

    def test_string_emp_cell_unchanged(self) -> None:
        rec = ex._row_to_record(tuple(_make_raw_row("171013")))
        self.assertEqual(rec["emp_id"], "171013")

    def test_non_integer_float_kept(self) -> None:
        # 정수형이 아닌 실수는 그대로(사번엔 없지만 _cell_str 일반 규칙 확인).
        self.assertEqual(ex._cell_str(3.5), "3.5")


class LookupNormalizationTests(unittest.TestCase):
    """숫자형 셀로 임포트된 사번을 로그인 스타일 '171013' 조회로 찾는다."""

    def _patch_records(self, records: list[dict]):
        return (
            patch.object(ex.files, "available_months", return_value=["2026-04"]),
            patch.object(ex.files, "month_file_paths", return_value=[Path("m.xlsx")]),
            patch.object(ex.parser, "_records_from_path", return_value=records),
        )

    def test_numeric_cell_import_matches_string_login(self) -> None:
        # 숫자형 사번 셀 → 파싱 후 rec["emp_id"] == "171013"
        numeric_rec = ex._row_to_record(tuple(_make_raw_row(221023.0)))
        self.assertEqual(numeric_rec["emp_id"], "221023")
        p1, p2, p3 = self._patch_records([numeric_rec])
        with p1, p2, p3:
            self.assertTrue(ex.employee_exists_in_any_month("221023"))
            profile = ex.employee_profile_from_any_month("221023")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "김민호")

    def test_string_cell_import_still_matches(self) -> None:
        string_rec = ex._row_to_record(tuple(_make_raw_row("221023")))
        p1, p2, p3 = self._patch_records([string_rec])
        with p1, p2, p3:
            self.assertTrue(ex.employee_exists_in_any_month("221023"))

    def test_unrelated_emp_not_matched(self) -> None:
        rec = ex._row_to_record(tuple(_make_raw_row(171013.0)))
        p1, p2, p3 = self._patch_records([rec])
        with p1, p2, p3:
            self.assertFalse(ex.employee_exists_in_any_month("999999"))


if __name__ == "__main__":
    unittest.main()
