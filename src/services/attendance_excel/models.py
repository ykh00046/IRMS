"""Domain models for the attendance Excel parser.

Pure data definitions only: column index constants, header mapping tables,
exception types, and the row/profile/summary dataclasses. This module does no
I/O and depends only on the standard library so it can be imported freely by
the other submodules (``files``, ``parser``, ``anomaly``, ``summary``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

ANNUAL_LEAVE_KEYWORDS = ("연차", "월차", "휴가", "반차", "유급", "공가")

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
COL_ATTENDANCE_CODE = 16
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

# 헤더 행1(세부 명칭)에서 바로 매핑되는 단순 컬럼.
_HEADER_SIMPLE_FIELDS = {
    "근무일자": "date",
    "요일": "weekday",
    "구분": "day_type",
    "사번": "emp_id",
    "성명": "name",
    "남여": "gender",
    "근무공장": "factory",
    "근무직구분": "job_type",
    "부서명": "department",
    "근무조구분": "shift_group",
    "근무타임": "shift_time",
    "근태코드": "attendance_code",
    "출근": "check_in",
    "퇴근": "check_out",
    "익일": "next_day",
    "지각시간": "late",
    "조퇴시간": "early_leave",
    "외출시간": "outing",
}
# 평일/휴일 근무시간 그룹은 '조출/정상/연장/야근' 세부 명칭이 두 번 나오므로
# 그룹 헤더(행0: 평일근무시간/휴일근무시간)로 구분한다.
_HEADER_WORKHOUR_SUFFIX = {"조출": "early", "정상": "normal", "연장": "overtime", "야근": "night"}
# 헤더 자동 매핑을 신뢰하려면 최소한 이 필드들이 모두 잡혀야 한다.
_HEADER_REQUIRED_FIELDS = frozenset(
    {"date", "emp_id", "name", "attendance_code", "check_in", "check_out", "shift_time", "day_type"}
)


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
    attendance_code: str = ""
    issues: list[str] = field(default_factory=list)
    has_issue: bool = False


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
    available_months_count: int = 0
    months_count: int = 0
    skipped_months: list[str] = field(default_factory=list)
    late_count: int = 0
    late_total: float = 0.0
    annual_leave_count: int = 0
    annual_leave_days: float = 0.0
    annual_leave_full_days: float = 0.0
    annual_leave_half_days: float = 0.0
    annual_leave_quarter_days: float = 0.0
