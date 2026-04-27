import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services import attendance_excel
from src.services.attendance_excel import AttendanceRow


def _row(
    *,
    attendance_code: str = "",
    date: str = "2026-04-24",
    check_in: str | None = "09:40",
    check_out: str | None = "17:20",
) -> AttendanceRow:
    return AttendanceRow(
        date=date,
        weekday="\uAE08",
        day_type="\uD3C9\uC77C",
        check_in=check_in,
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
        attendance_code=attendance_code,
    )


def _record(row: AttendanceRow, *, emp_id: str = "171013", name: str = "\uD14C\uC2A4\uD2B8") -> dict:
    return {
        "emp_id": emp_id,
        "name": name,
        "department": "\uC0DD\uC0B0",
        "shift_time": "\uC8FC\uAC04",
        "row": row,
    }


class AttendanceAnomalyResolutionTests(unittest.TestCase):
    def test_detect_month_anomalies_returns_unresolved_rows_across_month(self) -> None:
        fake_workbook = SimpleNamespace(
            sheetnames=[],
            active=object(),
            close=lambda: None,
        )
        records = [
            _record(_row(date="2026-04-23", check_in=None, check_out=None), emp_id="171013"),
            _record(
                _row(
                    date="2026-04-25",
                    attendance_code="\uC9C0\uAC01",
                    check_in="09:40",
                    check_out="17:20",
                ),
                emp_id="220903",
                name="\uAE40\uC138\uBBFC",
            ),
        ]

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=records),
        ):
            items = attendance_excel.detect_month_anomalies("2026-04")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["emp_id"], "171013")
        self.assertEqual(items[0]["dates"], ["2026-04-23"])
        self.assertIn("\uCD9C\uADFC \uB204\uB77D", items[0]["issues"])

    def test_detect_month_anomalies_merges_dates_and_dedupes_issues(self) -> None:
        records = [
            _record(
                _row(date="2026-04-25", check_in=None, check_out="18:00"),
                emp_id="171013",
            ),
            _record(
                _row(date="2026-04-21", check_in=None, check_out=None),
                emp_id="171013",
            ),
            _record(
                _row(date="2026-04-23", check_in="09:00", check_out=None),
                emp_id="171013",
            ),
        ]

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=records),
        ):
            items = attendance_excel.detect_month_anomalies("2026-04")

        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["emp_id"], "171013")
        self.assertEqual(item["dates"], ["2026-04-21", "2026-04-23", "2026-04-25"])
        self.assertEqual(
            sorted(item["issues"]),
            sorted(["출근 누락", "퇴근 누락"]),
        )

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
