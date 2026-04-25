import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services import attendance_excel
from src.services.attendance_excel import AttendanceRow


def _row(*, attendance_code: str = "") -> AttendanceRow:
    return AttendanceRow(
        date="2026-04-24",
        weekday="\uAE08",
        day_type="\uD3C9\uC77C",
        check_in="09:40",
        check_out="17:20",
        next_day=False,
        weekday_early=0.0,
        weekday_normal=8.0,
        weekday_overtime=0.0,
        weekday_night=0.0,
        holiday_early=0.0,
        holiday_normal=0.0,
        holiday_overtime=0.0,
        holiday_night=0.0,
        late_hours=0.0,
        early_leave_hours=0.0,
        outing_hours=0.0,
        note="",
        attendance_code=attendance_code,
    )


def _record(row: AttendanceRow) -> dict:
    return {
        "emp_id": "171013",
        "name": "\uD14C\uC2A4\uD2B8",
        "department": "\uC0DD\uC0B0",
        "shift_time": "\uC8FC\uAC04",
        "row": row,
    }


class AttendanceAnomalyResolutionTests(unittest.TestCase):
    def test_detect_today_anomalies_skips_rows_with_attendance_code(self) -> None:
        fake_workbook = SimpleNamespace(
            sheetnames=[],
            active=object(),
            close=lambda: None,
        )
        record = _record(_row(attendance_code="\uC9C0\uAC01"))

        with (
            patch.object(attendance_excel, "_load_workbook", return_value=fake_workbook),
            patch.object(attendance_excel, "_iter_data_rows", return_value=[object()]),
            patch.object(attendance_excel, "_row_to_record", return_value=record),
        ):
            day_type, items = attendance_excel.detect_today_anomalies(
                "2026-04", "2026-04-24"
            )

        self.assertEqual(day_type, "\uD3C9\uC77C")
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
