import unittest
from unittest.mock import patch

from src.services import attendance_excel
from src.services.attendance_excel import AttendanceRow


def _row(*, attendance_code: str = "", day_type: str = "평일", note: str = "") -> AttendanceRow:
    return AttendanceRow(
        date="2026-04-24",
        weekday="금",
        day_type=day_type,
        check_in=None,
        check_out=None,
        next_day=False,
        weekday_early=0.0,
        weekday_normal=0.0,
        weekday_overtime=0.0,
        weekday_night=0.0,
        holiday_early=0.0,
        holiday_normal=0.0,
        holiday_overtime=0.0,
        holiday_night=0.0,
        late_hours=0.0,
        early_leave_hours=0.0,
        outing_hours=0.0,
        note=note,
        attendance_code=attendance_code,
    )


class AttendanceAnnualLeaveTests(unittest.TestCase):
    def test_annual_leave_detects_attendance_code(self) -> None:
        self.assertTrue(attendance_excel._is_annual_leave_row(_row(attendance_code="연차")))

    def test_annual_leave_days_supports_full_half_and_quarter_day(self) -> None:
        self.assertEqual(attendance_excel._annual_leave_days(_row(attendance_code="연차")), 1.0)
        self.assertEqual(
            attendance_excel._annual_leave_days(_row(attendance_code="오전반차")),
            0.5,
        )
        self.assertEqual(
            attendance_excel._annual_leave_days(_row(attendance_code="반반차")),
            0.25,
        )

    def test_year_summary_aggregates_leave_day_totals(self) -> None:
        rows = [
            _row(attendance_code="연차"),
            _row(attendance_code="오후반차"),
            _row(attendance_code="반반차"),
        ]

        with (
            patch.object(attendance_excel, "available_months", return_value=["2026-04"]),
            patch.object(
                attendance_excel,
                "_load_month_rows_for_employee",
                return_value=(None, rows),
            ),
        ):
            summary = attendance_excel.load_year_summary_for_employee(2026, "171013")

        self.assertEqual(summary.annual_leave_days, 1.75)
        self.assertEqual(summary.annual_leave_full_days, 1.0)
        self.assertEqual(summary.annual_leave_half_days, 0.5)
        self.assertEqual(summary.annual_leave_quarter_days, 0.25)


if __name__ == "__main__":
    unittest.main()
