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

# Import submodules so attribute access ``attendance_excel.<submodule>.<name>``
# works (tests patch attributes on these submodule objects). Cross-module calls
# inside the package use ``from . import <submodule>`` then ``<submodule>.func()``
# so that ``unittest.mock.patch.object`` on a submodule attribute takes effect.
from . import anomaly
from . import files
from . import models
from . import parser
from . import summary

# --- models.py: COL_* constants, header map tables, exceptions, dataclasses ---
from .models import (
    ANNUAL_LEAVE_KEYWORDS,
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
    _HEADER_SIMPLE_FIELDS,
    _HEADER_WORKHOUR_SUFFIX,
    _HEADER_REQUIRED_FIELDS,
    AttendanceError,
    MonthFileNotFound,
    FileLocked,
    FileFormatInvalid,
    AttendanceRow,
    AttendanceProfile,
    AttendanceSummary,
    AttendanceAnnualSummary,
)

# --- files.py: path + time helpers + filesystem constants ---
from .files import (
    ATTENDANCE_DIR,
    FILENAME_PATTERN,
    FILENAME_REGEX,
    DEFAULT_COLUMNS,
    _canonical_month_file_path,
    _year_month_from_filename,
    month_file_paths,
    month_file_path,
    _month_file_paths_or_raise,
    available_months,
    current_year_month,
    current_date,
    _hhmm_to_minutes,
    _alert_reference_datetime,
    alert_year_month,
    _format_hours,
)

# --- parser.py: Excel reading ---
from .parser import (
    DAY_TYPE_VALUES,
    _cell_str,
    _cell_at,
    _cell_float,
    _cell_time,
    _make_column_map,
    _cell_day_type,
    _load_workbook,
    _iter_data_rows,
    _row_to_record,
    _column_map_from_ws,
    _records_from_path,
)

# --- anomaly.py: leave / shift / anomaly detection ---
from .anomaly import (
    PARTIAL_LEAVE_SHIFT_HOURS,
    PARTIAL_LEAVE_SHIFT_HOURS_BY_SHIFT,
    SHIFT2_SHIFT_TIMES,
    SHIFT2_REGULAR_WORK_MINUTES,
    SHIFT2_BREAK_MINUTES,
    SHIFT2_PARTIAL_LEAVE_MINUTES,
    SHIFT_BASELINES,
    ALERT_WORKDAY_TYPES,
    DAY_SHIFT_SHORT_LUNCH_DAY_TYPE,
    DAY_SHIFT_SHORT_LUNCH_CHECKOUT_OFFSET_MINUTES,
    ALERT_GRACE_MINUTES,
    FULL_DAY_LEAVE_KEYWORDS,
    ATTENDANCE_ISSUE_CODE_KEYWORDS,
    _is_full_day_leave,
    _partial_leave_shift,
    _compute_anomaly_baseline,
    _partial_leave_shift_minutes,
    _infer_partial_leave_half,
    _infer_shift2_partial_half,
    _compute_row_anomaly_baseline,
    _row_is_future,
    _baseline_has_passed,
    _append_issue,
    _attendance_code_issues,
    _deduction_code_mismatch_issues,
    _unprocessed_row_issues,
    _row_issue_labels,
    _display_date,
    _row_alert_category,
    _hide_detail_issue_text,
    _anomaly_detail,
    _merge_anomaly_record,
    detect_today_anomalies,
    detect_month_anomalies,
)

# --- summary.py: employee queries + serialization + annual-leave helpers ---
from .summary import (
    HALF_DAY_LEAVE_KEYWORDS,
    _attendance_row_key,
    _record_to_profile,
    _annual_leave_text,
    _is_annual_leave_row,
    _annual_leave_bucket,
    _annual_leave_days,
    employee_list,
    employee_exists_in_any_month,
    employee_profile_from_any_month,
    _load_month_rows_for_employee,
    load_month_for_employee,
    load_year_summary_for_employee,
    _summarize,
    serialize_profile,
    serialize_rows,
    serialize_summary,
    serialize_annual_summary,
)

__all__ = [
    # submodules (for patch.object(attendance_excel.<submodule>, ...))
    "anomaly",
    "files",
    "models",
    "parser",
    "summary",
    # models
    "ANNUAL_LEAVE_KEYWORDS",
    "COL_DATE",
    "COL_WEEKDAY",
    "COL_DAY_TYPE",
    "COL_EMP_ID",
    "COL_GENDER",
    "COL_FACTORY",
    "COL_NAME",
    "COL_JOB_TYPE",
    "COL_DEPARTMENT",
    "COL_SHIFT_GROUP",
    "COL_SHIFT_TIME",
    "COL_ATTENDANCE_CODE",
    "COL_CHECK_IN",
    "COL_CHECK_OUT",
    "COL_NEXT_DAY",
    "COL_WD_EARLY",
    "COL_WD_NORMAL",
    "COL_WD_OVERTIME",
    "COL_WD_NIGHT",
    "COL_HD_EARLY",
    "COL_HD_NORMAL",
    "COL_HD_OVERTIME",
    "COL_HD_NIGHT",
    "COL_LATE",
    "COL_EARLY_LEAVE",
    "COL_OUTING",
    "COL_NOTE",
    "_HEADER_SIMPLE_FIELDS",
    "_HEADER_WORKHOUR_SUFFIX",
    "_HEADER_REQUIRED_FIELDS",
    "AttendanceError",
    "MonthFileNotFound",
    "FileLocked",
    "FileFormatInvalid",
    "AttendanceRow",
    "AttendanceProfile",
    "AttendanceSummary",
    "AttendanceAnnualSummary",
    # files
    "ATTENDANCE_DIR",
    "FILENAME_PATTERN",
    "FILENAME_REGEX",
    "DEFAULT_COLUMNS",
    "_canonical_month_file_path",
    "_year_month_from_filename",
    "month_file_paths",
    "month_file_path",
    "_month_file_paths_or_raise",
    "available_months",
    "current_year_month",
    "current_date",
    "_hhmm_to_minutes",
    "_alert_reference_datetime",
    "alert_year_month",
    "_format_hours",
    # parser
    "DAY_TYPE_VALUES",
    "_cell_str",
    "_cell_at",
    "_cell_float",
    "_cell_time",
    "_make_column_map",
    "_cell_day_type",
    "_load_workbook",
    "_iter_data_rows",
    "_row_to_record",
    "_column_map_from_ws",
    "_records_from_path",
    # anomaly
    "PARTIAL_LEAVE_SHIFT_HOURS",
    "PARTIAL_LEAVE_SHIFT_HOURS_BY_SHIFT",
    "SHIFT2_SHIFT_TIMES",
    "SHIFT2_REGULAR_WORK_MINUTES",
    "SHIFT2_BREAK_MINUTES",
    "SHIFT2_PARTIAL_LEAVE_MINUTES",
    "SHIFT_BASELINES",
    "ALERT_WORKDAY_TYPES",
    "DAY_SHIFT_SHORT_LUNCH_DAY_TYPE",
    "DAY_SHIFT_SHORT_LUNCH_CHECKOUT_OFFSET_MINUTES",
    "ALERT_GRACE_MINUTES",
    "FULL_DAY_LEAVE_KEYWORDS",
    "ATTENDANCE_ISSUE_CODE_KEYWORDS",
    "_is_full_day_leave",
    "_partial_leave_shift",
    "_compute_anomaly_baseline",
    "_partial_leave_shift_minutes",
    "_infer_partial_leave_half",
    "_infer_shift2_partial_half",
    "_compute_row_anomaly_baseline",
    "_row_is_future",
    "_baseline_has_passed",
    "_append_issue",
    "_attendance_code_issues",
    "_deduction_code_mismatch_issues",
    "_unprocessed_row_issues",
    "_row_issue_labels",
    "_display_date",
    "_row_alert_category",
    "_hide_detail_issue_text",
    "_anomaly_detail",
    "_merge_anomaly_record",
    "detect_today_anomalies",
    "detect_month_anomalies",
    # summary
    "HALF_DAY_LEAVE_KEYWORDS",
    "_attendance_row_key",
    "_record_to_profile",
    "_annual_leave_text",
    "_is_annual_leave_row",
    "_annual_leave_bucket",
    "_annual_leave_days",
    "employee_list",
    "employee_exists_in_any_month",
    "employee_profile_from_any_month",
    "_load_month_rows_for_employee",
    "load_month_for_employee",
    "load_year_summary_for_employee",
    "_summarize",
    "serialize_profile",
    "serialize_rows",
    "serialize_summary",
    "serialize_annual_summary",
]
