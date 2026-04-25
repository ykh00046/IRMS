import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import attendance_excel


class FakeDir:
    def __init__(self, names: list[str]) -> None:
        self._entries = [Path(name) for name in names]

    def exists(self) -> bool:
        return True

    def iterdir(self):
        return iter(self._entries)


class FakeWorkbook:
    def __init__(self, token: str) -> None:
        self.sheetnames = ["Sheet1"]
        self.active = token
        self._token = token

    def __getitem__(self, _name: str):
        return self._token

    def close(self) -> None:
        return None


def _make_record(
    *,
    emp_id: str,
    name: str,
    date: str = "2026-04-24",
    day_type: str = "평일",
    check_in: str | None = "09:00",
    check_out: str | None = "18:00",
    late_hours: float = 0.0,
    early_leave_hours: float = 0.0,
    department: str = "생산팀",
    shift_time: str = "주간",
) -> dict:
    return {
        "emp_id": emp_id,
        "name": name,
        "department": department,
        "factory": "본공장",
        "shift_time": shift_time,
        "shift_group": "A",
        "job_type": "생산",
        "gender": "M",
        "row": attendance_excel.AttendanceRow(
            date=date,
            weekday="목",
            day_type=day_type,
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
            late_hours=late_hours,
            early_leave_hours=early_leave_hours,
            outing_hours=0.0,
            note="",
        ),
    }


class AttendanceExcelMultiSourceTests(unittest.TestCase):
    def test_month_file_paths_and_available_months_include_colorist_source(self) -> None:
        fake_dir = FakeDir(
            [
                "monthly_attendance_2026-04.xlsx",
                "monthly_attendance_colorist_2026-04.xlsx",
                "monthly_attendance_colorist_2026-03.xlsx",
            ]
        )

        with patch.object(attendance_excel, "ATTENDANCE_DIR", fake_dir):
            paths = attendance_excel.month_file_paths("2026-04")
            months = attendance_excel.available_months()

        self.assertEqual(
            [path.name for path in paths],
            [
                "monthly_attendance_2026-04.xlsx",
                "monthly_attendance_colorist_2026-04.xlsx",
            ],
        )
        self.assertEqual(months, ["2026-04", "2026-03"])

    def test_employee_queries_use_all_month_sources(self) -> None:
        records = {
            "base": _make_record(emp_id="100", name="메인직원"),
            "colorist": _make_record(emp_id="200", name="컬러직원", department="조색팀"),
        }

        def load_workbook(path: Path):
            return FakeWorkbook(path.stem)

        def iter_rows(token):
            return [(token,)]

        def row_to_record(raw):
            return records[raw[0]]

        with (
            patch.object(attendance_excel, "available_months", return_value=["2026-04"]),
            patch.object(
                attendance_excel,
                "month_file_paths",
                return_value=[Path("base.xlsx"), Path("colorist.xlsx")],
            ),
            patch.object(attendance_excel, "_load_workbook", side_effect=load_workbook),
            patch.object(attendance_excel, "_iter_data_rows", side_effect=iter_rows),
            patch.object(attendance_excel, "_row_to_record", side_effect=row_to_record),
        ):
            employees = attendance_excel.employee_list("2026-04")
            exists = attendance_excel.employee_exists_in_any_month("200")
            profile, rows, _summary = attendance_excel.load_month_for_employee("2026-04", "200")

        self.assertEqual({item["emp_id"] for item in employees}, {"100", "200"})
        self.assertTrue(exists)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.name, "컬러직원")
        self.assertEqual(len(rows), 1)

    def test_detect_today_anomalies_combines_all_month_sources(self) -> None:
        records = {
            "base": _make_record(emp_id="100", name="메인직원", check_in=None),
            "colorist": _make_record(
                emp_id="200",
                name="컬러직원",
                check_in="09:40",
                late_hours=0.0,
            ),
        }

        def load_workbook(path: Path):
            return FakeWorkbook(path.stem)

        def iter_rows(token):
            return [(token,)]

        def row_to_record(raw):
            return records[raw[0]]

        with (
            patch.object(
                attendance_excel,
                "month_file_paths",
                return_value=[Path("base.xlsx"), Path("colorist.xlsx")],
            ),
            patch.object(attendance_excel, "_load_workbook", side_effect=load_workbook),
            patch.object(attendance_excel, "_iter_data_rows", side_effect=iter_rows),
            patch.object(attendance_excel, "_row_to_record", side_effect=row_to_record),
        ):
            day_type, items = attendance_excel.detect_today_anomalies("2026-04", "2026-04-24")

        self.assertEqual(day_type, "평일")
        self.assertEqual({item["emp_id"] for item in items}, {"100", "200"})


if __name__ == "__main__":
    unittest.main()
