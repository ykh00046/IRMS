import unittest

from src.services import attendance_excel


def _make_raw_row() -> list[object]:
    row: list[object] = [None] * 34
    row[attendance_excel.COL_DATE] = "2026-04-24"
    row[attendance_excel.COL_WEEKDAY] = "\uAE08"
    row[attendance_excel.COL_DAY_TYPE] = "\uD3C9\uC77C"
    row[attendance_excel.COL_EMP_ID] = "171013"
    row[attendance_excel.COL_GENDER] = "\uB0A8"
    row[attendance_excel.COL_FACTORY] = "A\uAD00"
    row[attendance_excel.COL_NAME] = "\uAE40\uBBFC\uD638"
    row[attendance_excel.COL_JOB_TYPE] = "\uC0DD\uC0B0\uC9C1"
    row[attendance_excel.COL_DEPARTMENT] = "\uC790\uC7AC\uC0DD\uC0B0\uD300"
    row[attendance_excel.COL_SHIFT_GROUP] = "\uC815\uC0C1"
    row[attendance_excel.COL_SHIFT_TIME] = "\uC8FC\uAC04"
    row[attendance_excel.COL_CHECK_IN] = "09:00"
    row[attendance_excel.COL_CHECK_OUT] = "18:00"
    row[attendance_excel.COL_NEXT_DAY] = 0
    row[attendance_excel.COL_WD_EARLY] = 0
    row[attendance_excel.COL_WD_NORMAL] = 8
    row[attendance_excel.COL_WD_OVERTIME] = 0
    row[attendance_excel.COL_WD_NIGHT] = 0
    row[attendance_excel.COL_HD_EARLY] = 0
    row[attendance_excel.COL_HD_NORMAL] = 0
    row[attendance_excel.COL_HD_OVERTIME] = 0
    row[attendance_excel.COL_HD_NIGHT] = 0
    row[attendance_excel.COL_LATE] = 0
    row[attendance_excel.COL_EARLY_LEAVE] = 0
    row[attendance_excel.COL_OUTING] = 0
    row[attendance_excel.COL_NOTE] = ""
    return row


class AttendanceExcelRowDetailsTests(unittest.TestCase):
    def test_row_to_record_includes_attendance_code(self) -> None:
        raw = _make_raw_row()
        raw[attendance_excel.COL_ATTENDANCE_CODE] = "\uC624\uC804\uBC18\uCC28"

        record = attendance_excel._row_to_record(tuple(raw))

        self.assertEqual(record["row"].attendance_code, "\uC624\uC804\uBC18\uCC28")
        self.assertFalse(record["row"].has_issue)
        self.assertEqual(record["row"].issues, [])

    def test_row_to_record_only_marks_unprocessed_issues(self) -> None:
        raw = _make_raw_row()
        raw[attendance_excel.COL_CHECK_IN] = "09:40"
        raw[attendance_excel.COL_CHECK_OUT] = "17:20"
        raw[attendance_excel.COL_OUTING] = 1.239

        record = attendance_excel._row_to_record(tuple(raw))
        row = record["row"]

        self.assertTrue(row.has_issue)
        self.assertEqual(
            row.issues,
            ["\uC9C0\uAC01 \uBBF8\uCC98\uB9AC", "\uC870\uD1F4 \uBBF8\uCC98\uB9AC"],
        )

    def test_row_to_record_with_attendance_code_is_not_marked_as_issue(self) -> None:
        raw = _make_raw_row()
        raw[attendance_excel.COL_CHECK_IN] = "09:40"
        raw[attendance_excel.COL_CHECK_OUT] = "17:20"
        raw[attendance_excel.COL_ATTENDANCE_CODE] = "\uC9C0\uAC01"

        record = attendance_excel._row_to_record(tuple(raw))
        row = record["row"]

        self.assertFalse(row.has_issue)
        self.assertEqual(row.issues, [])


if __name__ == "__main__":
    unittest.main()
