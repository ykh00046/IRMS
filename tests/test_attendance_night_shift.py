"""BUG-1 회귀: 2교대(야간) 퇴근 baseline 자정 넘김 오탐 방지.

야간조 퇴근 baseline 은 27:45(익일 03:45)라 실제로 다음 날력에 걸친다.
근무일 D 의 야간행을 D+1 새벽에 평가할 때, 아직 정상 근무 중(03:45 이전)이면
'퇴근 누락'으로 잡으면 안 된다. _baseline_has_passed 가 날짜 지름길 대신
근무일 자정 + baseline 실제 시각으로 비교하는지 고정한다.
"""

import datetime
import unittest

from src.services import attendance_excel as ex


def _night_row(check_out: str | None) -> ex.AttendanceRow:
    return ex.AttendanceRow(
        date="2026-07-20", weekday="월", day_type="평일",
        check_in="19:00", check_out=check_out, next_day=True,
        weekday_early=0, weekday_normal=0, weekday_overtime=0, weekday_night=0,
        holiday_early=0, holiday_normal=0, holiday_overtime=0, holiday_night=0,
        late_hours=0, early_leave_hours=0, outing_hours=0, note="",
        attendance_code="",
    )


class NightShiftBaselineTests(unittest.TestCase):
    SHIFT = "2교대(야간)"

    def test_dawn_before_checkout_baseline_not_flagged(self) -> None:
        # 익일 02:00 — 정규 퇴근(익일 03:45) 이전이므로 퇴근 누락 아님.
        ref = datetime.datetime(2026, 7, 21, 2, 0)
        issues = ex._unprocessed_row_issues(_night_row(None), self.SHIFT, reference=ref)
        self.assertNotIn("퇴근 누락", issues)

    def test_after_checkout_baseline_flagged(self) -> None:
        # 익일 04:00 — 정규 퇴근 + 유예(03:45 + 15m = 04:00) 도달 → 퇴근 누락.
        ref = datetime.datetime(2026, 7, 21, 4, 0)
        issues = ex._unprocessed_row_issues(_night_row(None), self.SHIFT, reference=ref)
        self.assertIn("퇴근 누락", issues)

    def test_checkout_present_never_flagged_at_dawn(self) -> None:
        ref = datetime.datetime(2026, 7, 21, 4, 30)
        issues = ex._unprocessed_row_issues(_night_row("03:40"), self.SHIFT, reference=ref)
        self.assertNotIn("퇴근 누락", issues)

    def test_baseline_has_passed_crosses_midnight(self) -> None:
        # 직접 단위 검증: base_out=1665(익일 03:45), 유예 15m → 익일 04:00 도래.
        row = _night_row(None)
        base_out = ex.SHIFT_BASELINES[self.SHIFT][1]
        self.assertFalse(
            ex._baseline_has_passed(row, base_out, datetime.datetime(2026, 7, 21, 2, 0))
        )
        self.assertTrue(
            ex._baseline_has_passed(row, base_out, datetime.datetime(2026, 7, 21, 4, 0))
        )

    def test_same_day_daytime_baseline_unchanged(self) -> None:
        # 주간 회귀: 같은 날 09:15 유예 경계가 그대로 동작.
        day_row = ex.AttendanceRow(
            date="2026-07-20", weekday="월", day_type="평일",
            check_in=None, check_out=None, next_day=False,
            weekday_early=0, weekday_normal=0, weekday_overtime=0, weekday_night=0,
            holiday_early=0, holiday_normal=0, holiday_overtime=0, holiday_night=0,
            late_hours=0, early_leave_hours=0, outing_hours=0, note="",
            attendance_code="",
        )
        base_in = ex.SHIFT_BASELINES["주간"][0]
        self.assertFalse(
            ex._baseline_has_passed(day_row, base_in, datetime.datetime(2026, 7, 20, 9, 10))
        )
        self.assertTrue(
            ex._baseline_has_passed(day_row, base_in, datetime.datetime(2026, 7, 20, 9, 20))
        )


if __name__ == "__main__":
    unittest.main()
