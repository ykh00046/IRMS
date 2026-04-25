import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services import attendance_excel
from src.services.attendance_excel import AttendanceRow


def _row(day_type: str, check_out: str) -> AttendanceRow:
    return AttendanceRow(
        date="2026-04-24",
        weekday="금",
        day_type=day_type,
        check_in="09:00",
        check_out=check_out,
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
    )


def _record(row: AttendanceRow) -> dict:
    return {
        "emp_id": "171013",
        "name": "테스트",
        "department": "생산",
        "shift_time": "주간",
        "row": row,
    }


class AttendanceWeekday2Tests(unittest.TestCase):
    def test_weekday2_day_shift_uses_1730_checkout_baseline(self) -> None:
        self.assertEqual(
            attendance_excel._compute_anomaly_baseline("주간", "평일2", ""),
            (9 * 60, 17 * 60 + 30),
        )

    def test_weekday2_day_shift_is_included_in_anomaly_detection(self) -> None:
        fake_workbook = SimpleNamespace(
            sheetnames=[],
            active=object(),
            close=lambda: None,
        )
        record = _record(_row("평일2", "17:20"))

        with (
            patch.object(attendance_excel, "_load_workbook", return_value=fake_workbook),
            patch.object(attendance_excel, "_iter_data_rows", return_value=[object()]),
            patch.object(attendance_excel, "_row_to_record", return_value=record),
        ):
            day_type, items = attendance_excel.detect_today_anomalies(
                "2026-04", "2026-04-24"
            )

        self.assertEqual(day_type, "평일2")
        self.assertEqual(len(items), 1)
        self.assertIn("조퇴 미처리", items[0]["issues"])


if __name__ == "__main__":
    unittest.main()
