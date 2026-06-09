"""2026-06 ERP 열 순서 변경 + 육아휴직 제외 회귀 테스트.

배경: 2026-06 부터 ERP가 신원/근무정보 블록(성명·근무타임·부서명 등)의 열
순서를 바꿔 내보내기 시작했다. 고정 인덱스로 읽으면 성명이 '근무공장'으로,
근무타임이 엉뚱한 값으로 잡혀 근태 이상 감지가 무력화되고, 동시에 육아휴직이
"출근/퇴근 누락"으로 오분류됐다. 헤더 기반 자동 매핑과 휴직 제외 키워드로
바로잡은 동작을 고정한다.
"""

import datetime
import unittest

from src.services import attendance_excel as ex

# 2026-06 헤더(그룹 행0 + 세부 행1)를 실제 파일과 동일한 위치로 재현.
_JUNE_HEADER_GROUP = [None] * 38
_JUNE_HEADER_GROUP[0] = "일자"
_JUNE_HEADER_GROUP[3] = "근무직유형"
_JUNE_HEADER_GROUP[4] = "사원"
_JUNE_HEADER_GROUP[6] = "근무정보"
_JUNE_HEADER_GROUP[14] = "승인"
_JUNE_HEADER_GROUP[15] = "근태정보"
_JUNE_HEADER_GROUP[21] = "평일근무시간"
_JUNE_HEADER_GROUP[25] = "휴일근무시간"
_JUNE_HEADER_GROUP[29] = "공제시간"
_JUNE_HEADER_GROUP[33] = "비고"

_JUNE_HEADER_SUB = [None] * 38
for _idx, _name in {
    0: "근무일자", 1: "요일", 2: "구분", 4: "사번", 5: "성명", 6: "근무지",
    7: "남여", 8: "근무공장", 9: "근무직구분", 10: "부서명", 11: "근무조구분",
    12: "급여유형", 13: "근무타임", 15: "근무", 16: "근태코드", 17: "출근",
    18: "퇴근", 19: "익일", 20: "총시간", 21: "조출", 22: "정상", 23: "연장",
    24: "야근", 25: "조출", 26: "정상", 27: "연장", 28: "야근", 29: "지각시간",
    30: "조퇴시간", 31: "외출시간",
}.items():
    _JUNE_HEADER_SUB[_idx] = _name


class ColumnMapTests(unittest.TestCase):
    def test_june_layout_maps_to_correct_indices(self) -> None:
        colmap = ex._make_column_map(tuple(_JUNE_HEADER_GROUP), tuple(_JUNE_HEADER_SUB))
        self.assertEqual(colmap["name"], 5)
        self.assertEqual(colmap["shift_time"], 13)
        self.assertEqual(colmap["department"], 10)
        self.assertEqual(colmap["gender"], 7)
        self.assertEqual(colmap["factory"], 8)
        self.assertEqual(colmap["attendance_code"], 16)
        self.assertEqual(colmap["check_in"], 17)
        self.assertEqual(colmap["weekday_normal"], 22)
        self.assertEqual(colmap["holiday_normal"], 26)
        self.assertEqual(colmap["note"], 33)

    def test_june_row_reads_real_name_and_shift(self) -> None:
        row = [None] * 38
        row[0] = "2026-06-02"
        row[2] = "평일"
        row[4] = "251110"
        row[5] = "최선미"
        row[8] = "C관(2공장)"
        row[10] = "원료생산팀"
        row[13] = "주간"
        row[16] = "육아휴직"
        colmap = ex._make_column_map(tuple(_JUNE_HEADER_GROUP), tuple(_JUNE_HEADER_SUB))
        rec = ex._row_to_record(tuple(row), colmap)
        self.assertEqual(rec["name"], "최선미")
        self.assertEqual(rec["shift_time"], "주간")
        self.assertEqual(rec["department"], "원료생산팀")

    def test_missing_header_falls_back_to_default_columns(self) -> None:
        # 헤더를 인식하지 못하면(빈 세부 행) 구버전 고정 인덱스로 폴백.
        colmap = ex._make_column_map(None, None)
        self.assertEqual(colmap, ex.DEFAULT_COLUMNS)


class ParentalLeaveExclusionTests(unittest.TestCase):
    def _leave_row(self, code: str) -> ex.AttendanceRow:
        return ex.AttendanceRow(
            date="2026-06-02", weekday="화", day_type="평일",
            check_in=None, check_out=None, next_day=False,
            weekday_early=0, weekday_normal=0, weekday_overtime=0, weekday_night=0,
            holiday_early=0, holiday_normal=0, holiday_overtime=0, holiday_night=0,
            late_hours=0, early_leave_hours=0, outing_hours=0, note="",
            attendance_code=code,
        )

    def test_parental_leave_is_full_day_leave(self) -> None:
        self.assertTrue(ex._is_full_day_leave("평일", "", "육아휴직"))

    def test_parental_leave_produces_no_anomaly(self) -> None:
        ref = datetime.datetime(2026, 6, 9, 15, 0)
        issues = ex._unprocessed_row_issues(self._leave_row("육아휴직"), "주간", reference=ref)
        self.assertEqual(issues, [])

    def test_generic_leave_of_absence_excluded(self) -> None:
        ref = datetime.datetime(2026, 6, 9, 15, 0)
        issues = ex._unprocessed_row_issues(self._leave_row("병가휴직"), "주간", reference=ref)
        self.assertEqual(issues, [])

    def test_real_absence_still_flagged(self) -> None:
        # 휴가 코드가 없는 진짜 미타각은 여전히 잡혀야 한다(과교정 방지).
        ref = datetime.datetime(2026, 6, 9, 15, 0)
        issues = ex._unprocessed_row_issues(self._leave_row(""), "주간", reference=ref)
        self.assertIn("출근 누락", issues)


if __name__ == "__main__":
    unittest.main()
