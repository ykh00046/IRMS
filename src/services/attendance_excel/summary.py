"""Employee queries, summaries, and serialization.

Aggregates parsed records into profiles, month summaries, and annual summaries,
plus the annual-leave bucketing helpers used by the year aggregation. Cross-
module calls go through submodule attribute access (``files.func()`` /
``parser.func()``) so ``unittest.mock.patch.object`` on a submodule attribute
takes effect.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from . import files
from . import parser
from .models import (
    ANNUAL_LEAVE_KEYWORDS,
    AttendanceAnnualSummary,
    AttendanceProfile,
    AttendanceRow,
    AttendanceSummary,
    FileLocked,
    FileFormatInvalid,
    MonthFileNotFound,
    normalize_emp_id,
)

# 반차(0.5일) 판정 키워드. "오전"/"오후"는 단독으로는 반일 신호가 아니다 —
# 전일 "연차" 행의 비고에 "오전 회의" 같은 문구가 섞이면 0.5일로 오분류되던
# 문제(GAP-5) 때문에, 감지 엔진(anomaly._partial_leave_shift)과 동일하게
# "반차" 계열 키워드가 있을 때만 반일로 본다. "오전반차"/"오후반차"/"반차 오전"
# 등은 모두 "반차"를 포함하므로 그대로 잡히고, 반반차는 아래에서 먼저 걸러진다.
HALF_DAY_LEAVE_KEYWORDS = ("반차",)


def _attendance_row_key(row: AttendanceRow) -> tuple[Any, ...]:
    data = asdict(row)
    normalized: list[tuple[str, Any]] = []
    for key, value in data.items():
        if isinstance(value, list):
            normalized.append((key, tuple(value)))
        else:
            normalized.append((key, value))
    return tuple(normalized)


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


def _annual_leave_text(row: AttendanceRow) -> str:
    return f"{row.day_type} {row.attendance_code} {row.note}".strip()


def _is_annual_leave_row(row: AttendanceRow) -> bool:
    text = _annual_leave_text(row)
    return any(keyword in text for keyword in ANNUAL_LEAVE_KEYWORDS)


def _annual_leave_bucket(row: AttendanceRow) -> tuple[str, float]:
    text = _annual_leave_text(row)
    if "반반차" in text:
        return "quarter", 0.25
    if any(keyword in text for keyword in HALF_DAY_LEAVE_KEYWORDS):
        return "half", 0.5
    return "full", 1.0


def _annual_leave_days(row: AttendanceRow) -> float:
    _bucket, days = _annual_leave_bucket(row)
    return days


def employee_list(year_month: str) -> list[dict[str, str]]:
    """Return distinct employees present in the given month's file."""
    seen: dict[str, dict[str, str]] = {}
    for path in files._month_file_paths_or_raise(year_month):
        for rec in parser._records_from_path(path):
            if rec["emp_id"] not in seen:
                seen[rec["emp_id"]] = {
                    "emp_id": rec["emp_id"],
                    "name": rec["name"],
                    "department": rec["department"],
                    "factory": rec["factory"],
                }
    return sorted(seen.values(), key=lambda x: (x["department"], x["name"], x["emp_id"]))


def employee_exists_in_any_month(emp_id: str) -> bool:
    """Used during first login: accept any month that has this sa-beon.

    비교 양쪽을 ``normalize_emp_id`` 로 정규화해, 엑셀 사번이 숫자형(171013.0)
    으로 들어와도 로그인 사번 문자열과 일치시킨다(BUG-2).
    """
    target = normalize_emp_id(emp_id)
    if not target:
        return False
    for ym in files.available_months():
        for path in files.month_file_paths(ym):
            try:
                records = parser._records_from_path(path)
            except (MonthFileNotFound, FileLocked, FileFormatInvalid):
                continue
            for rec in records:
                if normalize_emp_id(rec["emp_id"]) == target:
                    return True
    return False


def employee_profile_from_any_month(emp_id: str) -> AttendanceProfile | None:
    """Find employee identity even when the selected month has no rows for them."""
    target = normalize_emp_id(emp_id)
    if not target:
        return None
    for ym in files.available_months():
        for path in files.month_file_paths(ym):
            try:
                records = parser._records_from_path(path)
            except (MonthFileNotFound, FileLocked, FileFormatInvalid):
                continue
            for rec in records:
                if rec and normalize_emp_id(rec["emp_id"]) == target:
                    return _record_to_profile(rec)
    return None


def _load_month_rows_for_employee(
    year_month: str, emp_id: str
) -> tuple[AttendanceProfile | None, list[AttendanceRow]]:
    """Return the rows present in one month without searching other months."""
    profile: AttendanceProfile | None = None
    rows: list[AttendanceRow] = []
    seen_rows: set[tuple[Any, ...]] = set()
    target = normalize_emp_id(emp_id)
    for path in files._month_file_paths_or_raise(year_month):
        for rec in parser._records_from_path(path):
            if normalize_emp_id(rec["emp_id"]) != target:
                continue
            if profile is None:
                profile = _record_to_profile(rec)
            row = rec["row"]
            row_key = _attendance_row_key(row)
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            rows.append(row)
    rows.sort(key=lambda row: (row.date, row.check_in or "", row.check_out or "", row.note))
    return profile, rows


def load_month_for_employee(
    year_month: str, emp_id: str
) -> tuple[AttendanceProfile | None, list[AttendanceRow], AttendanceSummary]:
    """Return (profile, rows, summary) for one employee in one month."""
    profile, rows = _load_month_rows_for_employee(year_month, emp_id)
    if profile is None:
        profile = employee_profile_from_any_month(emp_id)
    summary = _summarize(rows)
    return profile, rows, summary


def load_year_summary_for_employee(year: int, emp_id: str) -> AttendanceAnnualSummary:
    """Aggregate yearly late/annual-leave totals from available monthly files."""
    summary = AttendanceAnnualSummary(year=year)
    prefix = f"{year:04d}-"
    year_months = [ym for ym in files.available_months() if ym.startswith(prefix)]
    summary.available_months_count = len(year_months)
    for ym in year_months:
        try:
            _profile, rows = _load_month_rows_for_employee(ym, emp_id)
        except (MonthFileNotFound, FileLocked, FileFormatInvalid):
            summary.skipped_months.append(ym)
            continue
        if not rows:
            continue
        month_summary = _summarize(rows)
        summary.months_count += 1
        summary.late_count += month_summary.late_count
        summary.late_total += month_summary.late_total
        for row in rows:
            if _is_annual_leave_row(row):
                bucket, days = _annual_leave_bucket(row)
                summary.annual_leave_count += 1
                summary.annual_leave_days += days
                if bucket == "quarter":
                    summary.annual_leave_quarter_days += days
                elif bucket == "half":
                    summary.annual_leave_half_days += days
                else:
                    summary.annual_leave_full_days += days
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
