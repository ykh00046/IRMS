import unittest
import datetime as dt
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


def _record(
    row: AttendanceRow,
    *,
    emp_id: str = "171013",
    name: str = "\uD14C\uC2A4\uD2B8",
    shift_time: str = "\uC8FC\uAC04",
) -> dict:
    return {
        "emp_id": emp_id,
        "name": name,
        "department": "\uC0DD\uC0B0",
        "shift_time": shift_time,
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
                    attendance_code="\uC5F0\uCC28",
                    check_in=None,
                    check_out=None,
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
        self.assertEqual(items[0]["details"][0]["display_date"], "04-23")
        self.assertEqual(items[0]["details"][0]["code"], "1")
        self.assertEqual(items[0]["details"][0]["content"], "출/퇴근 미타각")
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
            [detail["display_date"] for detail in item["details"]],
            ["04-21", "04-23", "04-25"],
        )
        self.assertEqual(
            sorted(item["issues"]),
            sorted(["출근 누락", "퇴근 누락"]),
        )

    def test_detect_today_anomalies_reports_processed_late_rows(self) -> None:
        fake_workbook = SimpleNamespace(
            sheetnames=[],
            active=object(),
            close=lambda: None,
        )
        row = _row(attendance_code="\uC9C0\uAC01")
        row.late_hours = 0.5
        record = _record(row)

        with (
            patch.object(attendance_excel, "_load_workbook", return_value=fake_workbook),
            patch.object(attendance_excel, "_iter_data_rows", return_value=[object()]),
            patch.object(attendance_excel, "_row_to_record", return_value=record),
        ):
            day_type, items = attendance_excel.detect_today_anomalies(
                "2026-04", "2026-04-24"
            )

        self.assertEqual(day_type, "\uD3C9\uC77C")
        self.assertEqual(len(items), 1)
        self.assertIn("\uC9C0\uAC01 0.5\uC2DC\uAC04", items[0]["issues"])

    def test_month_anomaly_detail_hides_processed_late_reason_text(self) -> None:
        row = _row(attendance_code="지각", date="2026-05-21", check_in="07:39", check_out="19:10")
        row.late_hours = 0.75
        record = _record(row, emp_id="260445", name="박종휘", shift_time="2교대(주간)")

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            items = attendance_excel.detect_month_anomalies("2026-05")

        self.assertEqual(items[0]["details"][0]["content"], "근태 이상")
        self.assertEqual(items[0]["details"][0]["extra_content"], "")
        self.assertIn("지각 0.75시간", items[0]["issues"])

    def test_detect_today_anomalies_skips_full_day_leave_code(self) -> None:
        record = _record(_row(attendance_code="\uC5F0\uCC28", check_in=None, check_out=None))

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            _day_type, items = attendance_excel.detect_today_anomalies(
                "2026-04", "2026-04-24"
            )

        self.assertEqual(items, [])

    def test_detect_today_anomalies_skips_training_absence(self) -> None:
        record = _record(
            _row(attendance_code="\uD6C8\uB828", check_in=None, check_out=None)
        )
        record["row"].note = "\uC608\uBE44\uAD70_8\uC2DC\uAC04"

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            _day_type, items = attendance_excel.detect_today_anomalies(
                "2026-04", "2026-04-24"
            )

        self.assertEqual(items, [])

    def test_unknown_half_quarter_leave_infers_afternoon_from_checkout(self) -> None:
        row = _row(attendance_code="반반차", check_in="08:30", check_out="16:01")
        record = _record(row, emp_id="240910", name="박효빈")

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            items = attendance_excel.detect_month_anomalies("2026-05")

        self.assertEqual(items, [])

    def test_partial_leave_processed_early_leave_time_is_not_anomaly_when_checkout_matches(self) -> None:
        row = _row(
            attendance_code="반차",
            date="2026-05-27",
            check_in="08:42",
            check_out="13:00",
        )
        row.early_leave_hours = 4.0
        record = _record(row, emp_id="240910", name="박효빈")

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            items = attendance_excel.detect_month_anomalies("2026-05")

        self.assertEqual(items, [])

    def test_two_shift_day_half_leave_infers_afternoon_from_checkout(self) -> None:
        row = _row(
            attendance_code="반차",
            date="2026-05-11",
            check_in="06:48",
            check_out="11:07",
        )
        record = _record(
            row,
            emp_id="250612",
            name="이시현",
            shift_time="2교대(주간)",
        )

        with (
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            items = attendance_excel.detect_month_anomalies("2026-05")

        self.assertEqual(items, [])

    def test_today_checkout_missing_waits_until_checkout_baseline_passes(self) -> None:
        today = attendance_excel.current_date()
        record = _record(_row(date=today, check_in="09:00", check_out=None))

        with (
            patch.object(
                attendance_excel,
                "_alert_reference_datetime",
                return_value=dt.datetime.fromisoformat(f"{today}T16:00:00"),
            ),
            patch.object(attendance_excel, "_month_file_paths_or_raise", return_value=[Path("dummy.xlsx")]),
            patch.object(attendance_excel, "_records_from_path", return_value=[record]),
        ):
            _day_type, items = attendance_excel.detect_today_anomalies(
                today[:7], today
            )

        self.assertEqual(items, [])

    def test_alert_year_month_falls_back_to_latest_available_file(self) -> None:
        with (
            patch.object(attendance_excel, "current_year_month", return_value="2026-05"),
            patch.object(attendance_excel, "month_file_paths", return_value=[]),
            patch.object(attendance_excel, "available_months", return_value=["2026-04", "2026-03"]),
        ):
            self.assertEqual(attendance_excel.alert_year_month(), "2026-04")


if __name__ == "__main__":
    unittest.main()
