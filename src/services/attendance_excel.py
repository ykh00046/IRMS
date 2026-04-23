"""Excel parser for monthly attendance files.

Source: ``C:\\ErpExcel\\monthly_attendance_YYYY-MM.xlsx`` (ERP exports nightly
at 18:00). The file is read on every request because it is small (~85KB for
~500 rows) and using a cache would risk showing stale data right after the
nightly refresh.

Columns (0-index on Sheet1, headers are on rows 0-1 so data starts at row 2):

    0  근무일자           11  부서명              22  평일 정상
    1  요일               12  근무조구분          23  평일 연장 (급여 1.5배)
    2  구분 (평일/휴일 등) 14  근무타임            24  평일 야근 (급여 1.5배)
    4  사번               17  출근 HH:MM          25  휴일 조출
    6  남여               18  퇴근 HH:MM          26  휴일 정상
    7  근무공장           19  익일 (0/1)          27  휴일 연장 (급여 1.5배)
    8  성명               20  총시간 (표시 제외)  28  휴일 야근 (급여 1.5배)
    10 근무직구분         21  평일 조출           29  지각시간
                                                  30  조퇴시간
                                                  31  외출시간
                                                  33  비고
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

ATTENDANCE_DIR = Path(r"C:\ErpExcel")
FILENAME_PATTERN = "monthly_attendance_{year_month}.xlsx"
FILENAME_REGEX = re.compile(r"^monthly_attendance_(\d{4}-\d{2})\.xlsx$")

COL_DATE = 0
COL_WEEKDAY = 1
COL_DAY_TYPE = 2
COL_EMP_ID = 4
COL_GENDER = 6
COL_FACTORY = 7
COL_NAME = 8
COL_JOB_TYPE = 10
COL_DEPARTMENT = 11
COL_SHIFT_GROUP = 12
COL_SHIFT_TIME = 14
COL_CHECK_IN = 17
COL_CHECK_OUT = 18
COL_NEXT_DAY = 19
COL_WD_EARLY = 21
COL_WD_NORMAL = 22
COL_WD_OVERTIME = 23
COL_WD_NIGHT = 24
COL_HD_EARLY = 25
COL_HD_NORMAL = 26
COL_HD_OVERTIME = 27
COL_HD_NIGHT = 28
COL_LATE = 29
COL_EARLY_LEAVE = 30
COL_OUTING = 31
COL_NOTE = 33


class AttendanceError(Exception):
    """Base class for attendance parsing errors."""


class MonthFileNotFound(AttendanceError):
    pass


class FileLocked(AttendanceError):
    pass


class FileFormatInvalid(AttendanceError):
    pass


@dataclass
class AttendanceRow:
    date: str
    weekday: str
    day_type: str
    check_in: str | None
    check_out: str | None
    next_day: bool
    weekday_early: float
    weekday_normal: float
    weekday_overtime: float
    weekday_night: float
    holiday_early: float
    holiday_normal: float
    holiday_overtime: float
    holiday_night: float
    late_hours: float
    early_leave_hours: float
    outing_hours: float
    note: str


@dataclass
class AttendanceProfile:
    emp_id: str
    name: str
    department: str
    factory: str
    shift_time: str
    shift_group: str
    job_type: str
    gender: str


@dataclass
class AttendanceSummary:
    work_days: int = 0
    late_count: int = 0
    late_total: float = 0.0
    early_leave_count: int = 0
    early_leave_total: float = 0.0
    outing_count: int = 0
    outing_total: float = 0.0
    weekday_early: float = 0.0
    weekday_normal: float = 0.0
    weekday_overtime: float = 0.0
    weekday_night: float = 0.0
    holiday_early: float = 0.0
    holiday_normal: float = 0.0
    holiday_overtime: float = 0.0
    holiday_night: float = 0.0


@dataclass
class AttendanceAnnualSummary:
    year: int
    months_count: int = 0
    late_count: int = 0
    late_total: float = 0.0
    annual_leave_count: int = 0
    annual_leave_days: float = 0.0


def month_file_path(year_month: str) -> Path:
    return ATTENDANCE_DIR / FILENAME_PATTERN.format(year_month=year_month)


def available_months() -> list[str]:
    if not ATTENDANCE_DIR.exists():
        return []
    months: set[str] = set()
    for entry in ATTENDANCE_DIR.iterdir():
        match = FILENAME_REGEX.match(entry.name)
        if match:
            months.add(match.group(1))
    return sorted(months, reverse=True)


def current_year_month() -> str:
    today = _dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def current_date() -> str:
    return _dt.date.today().isoformat()


def detect_today_anomalies(
    year_month: str, target_date: str
) -> tuple[str, list[dict[str, Any]]]:
    """Return (day_type, anomalies) for ``target_date`` in the given month.

    ``day_type`` is the value from the 구분 column for the first row seen on
    that date (empty if the date has no rows). Anomalies are only produced
    when ``day_type == "평일"``; weekend and custom-holiday days deliberately
    return an empty list so the tray popup stays silent on non-working days.

    Detection rules per employee row on the weekday:
      - check_in 없음           → "출근 누락"
      - check_out 없음          → "퇴근 누락"
      - late_hours > 0          → "지각 {:g}시간"
      - early_leave_hours > 0   → "조퇴 {:g}시간"

    Raises ``MonthFileNotFound`` or ``FileLocked`` on I/O issues.
    """

    path = month_file_path(year_month)
    wb = _load_workbook(path)
    try:
        ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.active
        day_type = ""
        anomalies: list[dict[str, Any]] = []
        for raw in _iter_data_rows(ws):
            rec = _row_to_record(raw)
            if not rec:
                continue
            row: AttendanceRow = rec["row"]
            if row.date != target_date:
                continue
            if not day_type:
                day_type = row.day_type
            if row.day_type != "평일":
                continue
            if _is_annual_leave_row(row):
                continue
            issues: list[str] = []
            if not row.check_in:
                issues.append("출근 누락")
            if not row.check_out:
                issues.append("퇴근 누락")
            if row.late_hours > 0:
                issues.append(f"지각 {row.late_hours:g}시간")
            if row.early_leave_hours > 0:
                issues.append(f"조퇴 {row.early_leave_hours:g}시간")
            if issues:
                anomalies.append(
                    {
                        "emp_id": rec["emp_id"],
                        "name": rec["name"],
                        "department": rec["department"],
                        "issues": issues,
                    }
                )
    finally:
        wb.close()

    return day_type, anomalies


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M")
    return str(value).strip()


def _cell_at(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def _cell_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _cell_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M")
    if isinstance(value, _dt.datetime):
        return value.strftime("%H:%M")
    text = str(value).strip()
    return text or None


def _load_workbook(path: Path):
    if not path.exists():
        raise MonthFileNotFound(str(path))
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except PermissionError as exc:  # pragma: no cover - windows-specific
        raise FileLocked(str(path)) from exc
    except InvalidFileException as exc:
        raise FileFormatInvalid(str(path)) from exc


def _iter_data_rows(ws):
    for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
        if row_idx < 2:
            continue
        if not row:
            continue
        if _cell_at(row, COL_DATE) is None and _cell_at(row, COL_EMP_ID) is None:
            continue
        yield row


def _row_to_record(row: tuple[Any, ...]) -> dict[str, Any]:
    emp_id = _cell_str(_cell_at(row, COL_EMP_ID))
    if not emp_id:
        return {}
    return {
        "emp_id": emp_id,
        "name": _cell_str(_cell_at(row, COL_NAME)),
        "department": _cell_str(_cell_at(row, COL_DEPARTMENT)),
        "factory": _cell_str(_cell_at(row, COL_FACTORY)),
        "shift_time": _cell_str(_cell_at(row, COL_SHIFT_TIME)),
        "shift_group": _cell_str(_cell_at(row, COL_SHIFT_GROUP)),
        "job_type": _cell_str(_cell_at(row, COL_JOB_TYPE)),
        "gender": _cell_str(_cell_at(row, COL_GENDER)),
        "row": AttendanceRow(
            date=_cell_str(_cell_at(row, COL_DATE)),
            weekday=_cell_str(_cell_at(row, COL_WEEKDAY)),
            day_type=_cell_str(_cell_at(row, COL_DAY_TYPE)),
            check_in=_cell_time(_cell_at(row, COL_CHECK_IN)),
            check_out=_cell_time(_cell_at(row, COL_CHECK_OUT)),
            next_day=bool(_cell_float(_cell_at(row, COL_NEXT_DAY))),
            weekday_early=_cell_float(_cell_at(row, COL_WD_EARLY)),
            weekday_normal=_cell_float(_cell_at(row, COL_WD_NORMAL)),
            weekday_overtime=_cell_float(_cell_at(row, COL_WD_OVERTIME)),
            weekday_night=_cell_float(_cell_at(row, COL_WD_NIGHT)),
            holiday_early=_cell_float(_cell_at(row, COL_HD_EARLY)),
            holiday_normal=_cell_float(_cell_at(row, COL_HD_NORMAL)),
            holiday_overtime=_cell_float(_cell_at(row, COL_HD_OVERTIME)),
            holiday_night=_cell_float(_cell_at(row, COL_HD_NIGHT)),
            late_hours=_cell_float(_cell_at(row, COL_LATE)),
            early_leave_hours=_cell_float(_cell_at(row, COL_EARLY_LEAVE)),
            outing_hours=_cell_float(_cell_at(row, COL_OUTING)),
            note=_cell_str(_cell_at(row, COL_NOTE)),
        ),
    }


def _record_to_profile(rec: dict[str, Any]) -> AttendanceProfile:
    return AttendanceProfile(
        emp_id=rec["emp_id"],
        name=rec["name"],
        department=rec["department"],
        factory=rec["factory"],
        shift_time=rec["shift_time"],
        shift_group=rec["shift_group"],
        job_type=rec["job_type"],
        gender=rec["gender"],
    )


def _is_annual_leave_row(row: AttendanceRow) -> bool:
    text = f"{row.day_type} {row.note}".strip()
    return any(keyword in text for keyword in ("연차", "월차", "휴가", "반차"))


def _annual_leave_days(row: AttendanceRow) -> float:
    text = f"{row.day_type} {row.note}"
    if "반차" in text or "오전" in text or "오후" in text:
        return 0.5
    return 1.0


def employee_list(year_month: str) -> list[dict[str, str]]:
    """Return distinct employees present in the given month's file."""
    path = month_file_path(year_month)
    wb = _load_workbook(path)
    try:
        ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.active
        seen: dict[str, dict[str, str]] = {}
        for row in _iter_data_rows(ws):
            rec = _row_to_record(row)
            if not rec:
                continue
            if rec["emp_id"] not in seen:
                seen[rec["emp_id"]] = {
                    "emp_id": rec["emp_id"],
                    "name": rec["name"],
                    "department": rec["department"],
                    "factory": rec["factory"],
                }
    finally:
        wb.close()
    return sorted(seen.values(), key=lambda x: (x["department"], x["name"], x["emp_id"]))


def employee_exists_in_any_month(emp_id: str) -> bool:
    """Used during first login: accept any month that has this sa-beon."""
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return False
    for ym in available_months():
        path = month_file_path(ym)
        try:
            wb = _load_workbook(path)
        except (MonthFileNotFound, FileLocked, FileFormatInvalid):
            continue
        try:
            ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.active
            for row in _iter_data_rows(ws):
                if _cell_str(_cell_at(row, COL_EMP_ID)) == emp_id:
                    return True
        finally:
            wb.close()
    return False


def employee_profile_from_any_month(emp_id: str) -> AttendanceProfile | None:
    """Find employee identity even when the selected month has no rows for them."""
    emp_id = (emp_id or "").strip()
    if not emp_id:
        return None
    for ym in available_months():
        path = month_file_path(ym)
        try:
            wb = _load_workbook(path)
        except (MonthFileNotFound, FileLocked, FileFormatInvalid):
            continue
        try:
            ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.active
            for row in _iter_data_rows(ws):
                rec = _row_to_record(row)
                if rec and rec["emp_id"] == emp_id:
                    return _record_to_profile(rec)
        finally:
            wb.close()
    return None


def load_month_for_employee(
    year_month: str, emp_id: str
) -> tuple[AttendanceProfile | None, list[AttendanceRow], AttendanceSummary]:
    """Return (profile, rows, summary) for one employee in one month."""
    path = month_file_path(year_month)
    wb = _load_workbook(path)
    try:
        ws = wb["Sheet1"] if "Sheet1" in wb.sheetnames else wb.active
        rows: list[AttendanceRow] = []
        profile: AttendanceProfile | None = None
        for raw in _iter_data_rows(ws):
            rec = _row_to_record(raw)
            if not rec or rec["emp_id"] != emp_id:
                continue
            if profile is None:
                profile = _record_to_profile(rec)
            rows.append(rec["row"])
    finally:
        wb.close()

    if profile is None:
        profile = employee_profile_from_any_month(emp_id)
    summary = _summarize(rows)
    return profile, rows, summary


def load_year_summary_for_employee(year: int, emp_id: str) -> AttendanceAnnualSummary:
    """Aggregate yearly late/annual-leave totals from available monthly files."""
    summary = AttendanceAnnualSummary(year=year)
    prefix = f"{year:04d}-"
    for ym in available_months():
        if not ym.startswith(prefix):
            continue
        try:
            _profile, rows, month_summary = load_month_for_employee(ym, emp_id)
        except (MonthFileNotFound, FileLocked, FileFormatInvalid):
            continue
        if not rows:
            continue
        summary.months_count += 1
        summary.late_count += month_summary.late_count
        summary.late_total += month_summary.late_total
        for row in rows:
            if _is_annual_leave_row(row):
                summary.annual_leave_count += 1
                summary.annual_leave_days += _annual_leave_days(row)
    return summary


def _summarize(rows: list[AttendanceRow]) -> AttendanceSummary:
    s = AttendanceSummary()
    for r in rows:
        if r.check_in or r.check_out:
            s.work_days += 1
        if r.late_hours > 0:
            s.late_count += 1
            s.late_total += r.late_hours
        if r.early_leave_hours > 0:
            s.early_leave_count += 1
            s.early_leave_total += r.early_leave_hours
        if r.outing_hours > 0:
            s.outing_count += 1
            s.outing_total += r.outing_hours
        s.weekday_early += r.weekday_early
        s.weekday_normal += r.weekday_normal
        s.weekday_overtime += r.weekday_overtime
        s.weekday_night += r.weekday_night
        s.holiday_early += r.holiday_early
        s.holiday_normal += r.holiday_normal
        s.holiday_overtime += r.holiday_overtime
        s.holiday_night += r.holiday_night
    return s


def serialize_profile(profile: AttendanceProfile | None) -> dict[str, str] | None:
    return asdict(profile) if profile else None


def serialize_rows(rows: list[AttendanceRow]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]


def serialize_summary(summary: AttendanceSummary) -> dict[str, Any]:
    return asdict(summary)


def serialize_annual_summary(summary: AttendanceAnnualSummary) -> dict[str, Any]:
    return asdict(summary)
