"""Filesystem + clock helpers for monthly attendance files.

All ``Path``/time helpers and the directory/pattern constants live here. The
remaining configuration constants (``SHIFT_BASELINES``, ``ALERT_*`` etc.) that
are consumed by anomaly detection are owned by ``anomaly``.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

ATTENDANCE_DIR = Path(r"C:\ErpExcel")
FILENAME_PATTERN = "monthly_attendance_{year_month}.xlsx"
FILENAME_REGEX = re.compile(r"^monthly_attendance(?:_.+)?_(\d{4}-\d{2})\.xlsx$")

# 위 COL_* 상수는 2026-05 까지의 ERP 내보내기 열 순서를 기준으로 한다.
# 2026-06 부터 ERP가 신원/근무정보 블록(성명·근무타임·부서명 등)의 열 순서를
# 바꿔 내보내기 시작했다. 고정 인덱스로 읽으면 이름이 '근무공장' 값으로,
# 근무타임이 엉뚱한 값으로 잡혀 근태 이상 감지가 무력화된다. 그래서 실제
# 파일을 읽을 때는 헤더 행에서 열 위치를 자동으로 찾고(_make_column_map),
# 헤더를 못 찾으면 아래 기본값으로 폴백한다. (단위 테스트는 헤더 없이 위치
# 기반으로 행을 만들어 _row_to_record 에 직접 넘기므로 이 기본값을 쓴다.)
from .models import (  # noqa: E402  (module-level re-export used by parser/tests)
    COL_DATE,
    COL_WEEKDAY,
    COL_DAY_TYPE,
    COL_EMP_ID,
    COL_GENDER,
    COL_FACTORY,
    COL_NAME,
    COL_JOB_TYPE,
    COL_DEPARTMENT,
    COL_SHIFT_GROUP,
    COL_SHIFT_TIME,
    COL_ATTENDANCE_CODE,
    COL_CHECK_IN,
    COL_CHECK_OUT,
    COL_NEXT_DAY,
    COL_WD_EARLY,
    COL_WD_NORMAL,
    COL_WD_OVERTIME,
    COL_WD_NIGHT,
    COL_HD_EARLY,
    COL_HD_NORMAL,
    COL_HD_OVERTIME,
    COL_HD_NIGHT,
    COL_LATE,
    COL_EARLY_LEAVE,
    COL_OUTING,
    COL_NOTE,
)
from .models import MonthFileNotFound  # noqa: E402

DEFAULT_COLUMNS: dict[str, int] = {
    "date": COL_DATE,
    "weekday": COL_WEEKDAY,
    "day_type": COL_DAY_TYPE,
    "emp_id": COL_EMP_ID,
    "gender": COL_GENDER,
    "factory": COL_FACTORY,
    "name": COL_NAME,
    "job_type": COL_JOB_TYPE,
    "department": COL_DEPARTMENT,
    "shift_group": COL_SHIFT_GROUP,
    "shift_time": COL_SHIFT_TIME,
    "attendance_code": COL_ATTENDANCE_CODE,
    "check_in": COL_CHECK_IN,
    "check_out": COL_CHECK_OUT,
    "next_day": COL_NEXT_DAY,
    "weekday_early": COL_WD_EARLY,
    "weekday_normal": COL_WD_NORMAL,
    "weekday_overtime": COL_WD_OVERTIME,
    "weekday_night": COL_WD_NIGHT,
    "holiday_early": COL_HD_EARLY,
    "holiday_normal": COL_HD_NORMAL,
    "holiday_overtime": COL_HD_OVERTIME,
    "holiday_night": COL_HD_NIGHT,
    "late": COL_LATE,
    "early_leave": COL_EARLY_LEAVE,
    "outing": COL_OUTING,
    "note": COL_NOTE,
}


def _canonical_month_file_path(year_month: str) -> Path:
    return ATTENDANCE_DIR / FILENAME_PATTERN.format(year_month=year_month)


def _year_month_from_filename(name: str) -> str | None:
    match = FILENAME_REGEX.match(name)
    if not match:
        return None
    return match.group(1)


def month_file_paths(year_month: str) -> list[Path]:
    if not ATTENDANCE_DIR.exists():
        return []
    canonical_name = FILENAME_PATTERN.format(year_month=year_month)
    matches = [
        entry
        for entry in ATTENDANCE_DIR.iterdir()
        if _year_month_from_filename(entry.name) == year_month
    ]
    return sorted(
        matches,
        key=lambda entry: (0 if entry.name == canonical_name else 1, entry.name.lower()),
    )


def month_file_path(year_month: str) -> Path:
    paths = month_file_paths(year_month)
    if paths:
        return paths[0]
    return _canonical_month_file_path(year_month)


def _month_file_paths_or_raise(year_month: str) -> list[Path]:
    paths = month_file_paths(year_month)
    if paths:
        return paths
    raise MonthFileNotFound(str(_canonical_month_file_path(year_month)))


def available_months() -> list[str]:
    if not ATTENDANCE_DIR.exists():
        return []
    months: set[str] = set()
    for entry in ATTENDANCE_DIR.iterdir():
        year_month = _year_month_from_filename(entry.name)
        if year_month:
            months.add(year_month)
    return sorted(months, reverse=True)


def current_year_month() -> str:
    today = _dt.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def current_date() -> str:
    return _dt.date.today().isoformat()


def _hhmm_to_minutes(text: str | None) -> int | None:
    """ERP 시각 문자열을 '자정 기준 분'으로 변환.

    ERP는 야간조 퇴근을 익일 07:04처럼 ``31:04``로 기록하므로 단순
    문자열 비교가 불가능하다. 시:분을 읽어 분 단위 정수로 반환하고,
    파싱이 실패하면 ``None``을 돌려준다.
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        h_part, _, m_part = s.partition(":")
        return int(h_part) * 60 + int(m_part or 0)
    except ValueError:
        return None


def _alert_reference_datetime() -> _dt.datetime:
    return _dt.datetime.now()


def alert_year_month() -> str:
    current = current_year_month()
    if month_file_paths(current):
        return current
    months = available_months()
    return months[0] if months else current


def _format_hours(value: float) -> str:
    return f"{value:g}"
