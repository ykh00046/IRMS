"""Leave/shift/anomaly detection for attendance rows.

Owns the shift-baseline configuration and every function that decides whether
a row is anomalous (``_is_full_day_leave`` through ``detect_month_anomalies``).
Cross-module calls go through submodule attribute access (``files.func()`` /
``parser.func()``) so ``unittest.mock.patch.object`` on a submodule attribute
takes effect.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from . import files
from . import parser
from .models import (
    ANNUAL_LEAVE_KEYWORDS,
    AttendanceRow,
)

# 부분 휴가가 출/퇴근 기준 시각을 이동시키는 시간(시간 단위, 주간 9-18시 기준).
# 반차는 근무 4시간 + 점심 1시간을 덜어내므로 baseline 이 5시간 이동하고,
# 반반차는 2시간짜리라 점심과 겹치지 않아 2시간만 이동한다. 반반차가 반차의
# 부분 문자열이므로 탐지 시에는 반드시 "반반차"를 먼저 검사해야 한다.
#
# 검증 (주간 기준):
#   반차   오전 → 09:00 + 5h = 14:00 출근,  오후 → 18:00 - 5h = 13:00 퇴근
#   반반차 오전 → 09:00 + 2h = 11:00 출근,  오후 → 18:00 - 2h = 16:00 퇴근
PARTIAL_LEAVE_SHIFT_HOURS = (("반반차", 2), ("반차", 5))
PARTIAL_LEAVE_SHIFT_HOURS_BY_SHIFT = {
    "주간": {"반반차": 2, "반차": 5},
}

# 2교대 부분 휴가 규칙 (주간/야간 공통, 2026-06-17 현장 확인).
# 정규 근무 8시간 안에 휴식 45분(30분 + 채워야 하는 15분)이 포함되어,
# 잔업 없이 풀근무하면 출근 + 8h45m 에 퇴근한다(주간 15:45, 야간 27:45 = 익일 03:45).
# 휴식은 정규 근무 후반부(주간 13:00 이후)에 있어, 6시간 이하만 일하고
# 나가는 오후 반차/반반차에는 휴식이 붙지 않는다. 따라서:
#   - 오전(늦게 출근): 출근 = 출근기준 + 휴가량
#   - 오후(일찍 퇴근): 퇴근 = 출근기준 + (정규 8h − 휴가량)   ← 휴식 미포함
# 휴가량은 반차 4h / 반반차 2h (주간과 달리 점심 보정 없음).
#
# 검증 (2교대 주간, 출근 07:00):
#   반차   오전 → 07:00 + 4h = 11:00 출근,  오후 → 07:00 + (8−4)h = 11:00 퇴근
#   반반차 오전 → 07:00 + 2h = 09:00 출근,  오후 → 07:00 + (8−2)h = 13:00 퇴근
SHIFT2_SHIFT_TIMES = ("2교대(주간)", "2교대(야간)")
SHIFT2_REGULAR_WORK_MINUTES = 8 * 60   # 정규 근무(휴식 제외)
SHIFT2_BREAK_MINUTES = 45              # 정규 근무 중 휴식(30+15분) → 풀근무 시에만 퇴근에 가산
SHIFT2_PARTIAL_LEAVE_MINUTES = {"반차": 4 * 60, "반반차": 2 * 60}

# Baseline (출근, 퇴근) per shift_time, expressed in minutes-from-midnight.
# 퇴근 baseline이 1440 이상이면 익일 표기(ERP는 야간 퇴근을 24+ 시각으로 기록).
# 빈 shift_time은 교대조의 비번 날을 뜻하므로 anomaly 감지 대상에서 제외된다.
#
# 2교대 퇴근 기준은 "잔업 없이 정규 8시간 근무를 마친 시각"(출근 + 8h45m).
# 잔업은 선택이고 정규 근무를 채우면 근태 의무가 끝나므로, 잔업 끝(주간 19:00 /
# 야간 31:00)이 아니라 정규 퇴근(15:45 / 27:45)을 기준으로 둔다. 면제일 신호가
# ERP에 없어 19:00 기준이면 잔업 미실시 정상 퇴근이 전부 오탐이 되기 때문.
SHIFT_BASELINES = {
    "주간":        (9 * 60,  18 * 60),                                                   # 09:00 ~ 18:00 (평일만)
    "2교대(주간)": (7 * 60,  7 * 60 + SHIFT2_REGULAR_WORK_MINUTES + SHIFT2_BREAK_MINUTES),   # 07:00 ~ 15:45 (정규 퇴근)
    "2교대(야간)": (19 * 60, 19 * 60 + SHIFT2_REGULAR_WORK_MINUTES + SHIFT2_BREAK_MINUTES),  # 19:00 ~ 27:45 (익일 03:45, 정규)
}
ALERT_WORKDAY_TYPES = ("평일", "평일2")
DAY_TYPE_VALUES = ("평일", "평일2", "주휴", "무휴", "유휴")
DAY_SHIFT_SHORT_LUNCH_DAY_TYPE = "평일2"
DAY_SHIFT_SHORT_LUNCH_CHECKOUT_OFFSET_MINUTES = 30
ALERT_GRACE_MINUTES = 15

# Full-day leaves that exclude a row from anomaly detection entirely.
# (반차/반반차는 이 목록에 들어가지 않는다 — baseline이 이동할 뿐 여전히
# 감지 대상.)
# "휴직"은 육아휴직·병가휴직 등 장기 전일 부재를 포괄한다. 휴직 기간에는
# 출근/퇴근 기록이 없는 것이 정상이므로 "출근 누락" 등으로 잡으면 안 된다.
# "결근"도 근태코드로 명시되면 담당자가 이미 처리한 전일 부재이므로,
# 출근/퇴근 기록이 없는 것이 정상 — "미타각/출근 누락"으로 잡지 않는다.
FULL_DAY_LEAVE_KEYWORDS = ("연차", "월차", "휴가", "휴직", "유급", "공가", "훈련", "예비군", "교육", "결근")
ATTENDANCE_ISSUE_CODE_KEYWORDS = ("미타각", "누락")


def _is_full_day_leave(day_type: str, note: str, attendance_code: str = "") -> bool:
    """감지 대상에서 아예 제외할 '하루 전체 휴가' 판정.

    반차/반반차는 여기 포함되지 않는다. 하루 중 일부만 빠지는 휴가는
    baseline 만 이동시키고 감지는 계속 수행한다.
    """
    text = f"{day_type or ''} {note or ''} {attendance_code or ''}"
    if any(keyword in text for keyword, _hours in PARTIAL_LEAVE_SHIFT_HOURS):
        return False
    return any(keyword in text for keyword in FULL_DAY_LEAVE_KEYWORDS)


def _partial_leave_shift(
    day_type: str, note: str, attendance_code: str = ""
) -> tuple[str, str, int] | None:
    """반차·반반차 이동량(분)과 오전/오후 구분을 반환.

    반환 형식: ``(leave_kind, half, shift_minutes)``
      - leave_kind: "반차" 또는 "반반차"
      - half: "오전" / "오후" / "unknown"
      - shift_minutes: 해당 근무형태의 baseline을 이동시킬 분 단위

    해당 행에 부분 휴가 키워드가 없으면 ``None`` 반환. 반반차는 반차의
    부분 문자열이므로 반반차 먼저 검사한다.
    """
    text = f"{day_type or ''} {note or ''} {attendance_code or ''}"
    for keyword, hours in PARTIAL_LEAVE_SHIFT_HOURS:
        if keyword in text:
            if "오전" in text:
                half = "오전"
            elif "오후" in text:
                half = "오후"
            else:
                half = "unknown"
            return keyword, half, hours * 60
    return None


def _compute_anomaly_baseline(
    shift_time: str, day_type: str, note: str, attendance_code: str = ""
) -> tuple[int, int] | None:
    """shift_time과 부분 휴가를 반영한 ``(출근분, 퇴근분)`` baseline을 반환.

    shift_time이 알려진 근무형태가 아니면 ``None`` (감지 제외).
    부분 휴가(반차/반반차)는 주간 시프트에만 적용한다. 2교대는 근무
    구조가 달라 동일 규칙이 맞지 않으므로 일단 기본 baseline 그대로 쓴다.
    """
    baseline = SHIFT_BASELINES.get(shift_time)
    if baseline is None:
        return None
    base_in, base_out = baseline

    if shift_time == "주간":
        if day_type == DAY_SHIFT_SHORT_LUNCH_DAY_TYPE:
            base_out -= DAY_SHIFT_SHORT_LUNCH_CHECKOUT_OFFSET_MINUTES
        leave = _partial_leave_shift(day_type, note, attendance_code)
        if leave:
            _kind, half, shift_minutes = leave
            if half == "오전":
                base_in += shift_minutes
            elif half == "오후":
                base_out -= shift_minutes
            # half == "unknown" → 구분 모호. 보수적으로 기본 baseline 유지.
    return base_in, base_out


def _partial_leave_shift_minutes(
    shift_time: str,
    leave_kind: str,
    default_minutes: int,
) -> int:
    hours_by_kind = PARTIAL_LEAVE_SHIFT_HOURS_BY_SHIFT.get(shift_time)
    if not hours_by_kind:
        return default_minutes
    return int(hours_by_kind.get(leave_kind, default_minutes // 60)) * 60


def _infer_partial_leave_half(
    row: AttendanceRow,
    base_in: int,
    base_out: int,
    shift_minutes: int,
) -> str:
    """반차/반반차의 오전/오후 구분을 출퇴근 기록으로 추론(주간).

    오전 사용이면 출근이 ``base_in + 휴가량`` 이후로 늦다. 오후 사용이면
    정시 출근하고 정규 퇴근(``base_out``)보다 일찍 나간다 — 휴가량을 덜
    쓰고 늦게 나가도 '정규 퇴근 이전 퇴근'이면 오후 신호로 본다.
    """
    in_mins = files._hhmm_to_minutes(row.check_in)
    out_mins = files._hhmm_to_minutes(row.check_out)
    morning_in = base_in + shift_minutes

    if in_mins is not None and in_mins >= morning_in:
        return "오전"
    if out_mins is not None and out_mins < base_out:
        return "오후"
    return "unknown"


def _infer_shift2_partial_half(
    row: AttendanceRow,
    base_in: int,
    base_out: int,
    off_minutes: int,
) -> str:
    """2교대 반차/반반차의 오전/오후 구분을 출퇴근 기록으로 추론.

    오전 사용이면 출근이 ``base_in + 휴가량`` 이후로 늦다. 오후 사용이면
    정시 출근하고 정규 퇴근(``base_out``)보다 일찍 나간다. 모델상 기대
    퇴근(출근+(8h−휴가량))까지 채우지 않고 잔업을 일부 더 하고 나가면
    그 시각보다 늦을 수 있으므로, '정규 퇴근 이전 퇴근'을 오후 신호로 본다.
    """
    in_mins = files._hhmm_to_minutes(row.check_in)
    out_mins = files._hhmm_to_minutes(row.check_out)
    morning_in = base_in + off_minutes
    if in_mins is not None and in_mins >= morning_in:
        return "오전"
    if out_mins is not None and out_mins < base_out:
        return "오후"
    return "unknown"


def _compute_row_anomaly_baseline(
    row: AttendanceRow,
    shift_time: str,
) -> tuple[int, int] | None:
    baseline = SHIFT_BASELINES.get(shift_time)
    if baseline is None:
        return None
    base_in, base_out = baseline

    leave = _partial_leave_shift(row.day_type, row.note, row.attendance_code)

    if shift_time in SHIFT2_SHIFT_TIMES:
        # 2교대: 정규 8h(휴식 0.5h 포함) 모델. 잔업 구간은 휴가 대상이 아니므로
        # 오후 퇴근은 출근기준 + (정규 8h − 휴가량)으로 계산(휴식 미포함).
        if leave:
            leave_kind, half, _default = leave
            off = SHIFT2_PARTIAL_LEAVE_MINUTES.get(leave_kind, 0)
            if half == "unknown":
                half = _infer_shift2_partial_half(row, base_in, base_out, off)
            if half == "오전":
                base_in += off
            elif half == "오후":
                base_out = base_in + (SHIFT2_REGULAR_WORK_MINUTES - off)
        return base_in, base_out

    # 주간(09-18): 점심 1h 포함 대칭 모델.
    if shift_time in PARTIAL_LEAVE_SHIFT_HOURS_BY_SHIFT:
        if row.day_type == DAY_SHIFT_SHORT_LUNCH_DAY_TYPE:
            base_out -= DAY_SHIFT_SHORT_LUNCH_CHECKOUT_OFFSET_MINUTES
        if leave:
            leave_kind, half, default_shift_minutes = leave
            shift_minutes = _partial_leave_shift_minutes(
                shift_time, leave_kind, default_shift_minutes
            )
            if half == "unknown":
                half = _infer_partial_leave_half(row, base_in, base_out, shift_minutes)
            if half == "오전":
                base_in += shift_minutes
            elif half == "오후":
                base_out -= shift_minutes
    return base_in, base_out


def _row_is_future(row: AttendanceRow, reference: _dt.datetime) -> bool:
    try:
        row_date = _dt.date.fromisoformat(row.date)
    except ValueError:
        return False
    return row_date > reference.date()


def _baseline_has_passed(row: AttendanceRow, baseline_minutes: int, reference: _dt.datetime) -> bool:
    try:
        row_date = _dt.date.fromisoformat(row.date)
    except ValueError:
        return True
    if row_date < reference.date():
        return True
    if row_date > reference.date():
        return False
    reference_minutes = reference.hour * 60 + reference.minute
    return reference_minutes >= baseline_minutes + ALERT_GRACE_MINUTES


def _append_issue(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _attendance_code_issues(row: AttendanceRow) -> list[str]:
    code = str(row.attendance_code or "").strip()
    if not code:
        return []
    if not any(keyword in code for keyword in ATTENDANCE_ISSUE_CODE_KEYWORDS):
        return []

    issues: list[str] = []
    if "출" in code:
        _append_issue(issues, "출근 누락")
    if "퇴" in code:
        _append_issue(issues, "퇴근 누락")
    if not issues:
        _append_issue(issues, f"근태코드 확인: {code}")
    return issues


def _deduction_code_mismatch_issues(row: AttendanceRow) -> list[str]:
    code = str(row.attendance_code or "")
    has_deduction = (
        row.late_hours > 0
        or row.early_leave_hours > 0
        or row.outing_hours > 0
    )
    if not has_deduction:
        return []

    if any(keyword in code for keyword in ANNUAL_LEAVE_KEYWORDS):
        return ["공제시간 불일치"]

    issues: list[str] = []
    if row.late_hours > 0 and "지각" not in code:
        _append_issue(issues, "근태코드 누락(지각)")
    if row.early_leave_hours > 0 and "조퇴" not in code:
        _append_issue(issues, "근태코드 누락(조퇴)")
    if row.outing_hours > 0 and "외출" not in code:
        _append_issue(issues, "근태코드 누락(외출)")
    return issues


def _unprocessed_row_issues(
    row: AttendanceRow,
    shift_time: str,
    *,
    reference: _dt.datetime | None = None,
) -> list[str]:
    reference = reference or files._alert_reference_datetime()
    if _row_is_future(row, reference):
        return []
    if row.day_type not in ALERT_WORKDAY_TYPES:
        return []

    issues = _attendance_code_issues(row)
    for issue in _deduction_code_mismatch_issues(row):
        _append_issue(issues, issue)

    if _is_full_day_leave(row.day_type, row.note, row.attendance_code):
        return issues

    baseline = _compute_row_anomaly_baseline(row, shift_time)
    if baseline is None:
        return issues
    base_in, base_out = baseline

    if row.late_hours <= 0 and not row.check_in and _baseline_has_passed(row, base_in, reference):
        _append_issue(issues, "출근 누락")
    elif row.late_hours <= 0:
        in_mins = files._hhmm_to_minutes(row.check_in)
        if in_mins is not None and in_mins > base_in:
            _append_issue(issues, "지각 미처리")

    if row.early_leave_hours <= 0 and not row.check_out and _baseline_has_passed(row, base_out, reference):
        _append_issue(issues, "퇴근 누락")
    elif row.early_leave_hours <= 0:
        out_mins = files._hhmm_to_minutes(row.check_out)
        if out_mins is not None and out_mins < base_out:
            _append_issue(issues, "조퇴 미처리")

    return issues


def _row_issue_labels(row: AttendanceRow, shift_time: str) -> list[str]:
    return _unprocessed_row_issues(row, shift_time)


def _display_date(date_text: str) -> str:
    try:
        return _dt.date.fromisoformat(date_text).strftime("%m-%d")
    except ValueError:
        return date_text


def _row_alert_category(row: AttendanceRow, issues: list[str]) -> tuple[str, str]:
    joined = " / ".join(issues)
    has_check_in_missing = "\uCD9C\uADFC \uB204\uB77D" in issues
    has_check_out_missing = "\uD1F4\uADFC \uB204\uB77D" in issues

    if has_check_in_missing and has_check_out_missing:
        return "1", "\uCD9C/\uD1F4\uADFC \uBBF8\uD0C0\uAC01"
    if has_check_in_missing:
        return "2", "\uCD9C\uADFC \uBBF8\uD0C0\uAC01"
    if has_check_out_missing:
        return "3", "\uD1F4\uADFC \uBBF8\uD0C0\uAC01"

    has_code_deduction_mismatch = any(
        issue.startswith("\uADFC\uD0DC\uCF54\uB4DC \uB204\uB77D")
        or issue == "\uACF5\uC81C\uC2DC\uAC04 \uBD88\uC77C\uCE58"
        for issue in issues
    )
    if has_code_deduction_mismatch:
        return "0", "\uADFC\uD0DC \uC774\uC0C1"

    if any(issue.startswith("\uC9C0\uAC01 ") for issue in issues):
        return "4", "\uADFC\uD0DC \uC774\uC0C1"
    if any(issue.startswith("\uC870\uD1F4 ") for issue in issues):
        return "4", "\uADFC\uD0DC \uC774\uC0C1"

    has_unprocessed_late_or_leave = any(
        "\uBBF8\uCC98\uB9AC" in issue for issue in issues
    )
    if has_unprocessed_late_or_leave:
        basis = f"{row.day_type} {row.note} {row.attendance_code}"
        if "\uBC18\uBC18\uCC28" in basis:
            return "6", "\uADFC\uD0DC\uCF54\uB4DC \uB204\uB77D(\uBC18\uBC18\uCC28/\uC870\uD1F4 \uC608\uC0C1)"
        if "\uBC18\uCC28" in basis or "\uC870\uD1F4" in joined:
            return "5", "\uADFC\uD0DC\uCF54\uB4DC \uB204\uB77D(\uBC18\uCC28/\uC870\uD1F4 \uC608\uC0C1)"
        return "0", "\uAE30\uD0C0"

    return "0", "\uAE30\uD0C0"


def _hide_detail_issue_text(content: str, issues: list[str]) -> bool:
    return content == "\uADFC\uD0DC \uC774\uC0C1"


def _anomaly_detail(row: AttendanceRow, issues: list[str]) -> dict[str, Any]:
    code, content = _row_alert_category(row, issues)
    extra_content = " / ".join(issues)
    if extra_content == content or _hide_detail_issue_text(content, issues):
        extra_content = ""
    return {
        "date": row.date,
        "display_date": _display_date(row.date),
        "code": code,
        "content": content,
        "extra_content": extra_content,
        "status": "",
        "issues": list(issues),
    }


def _merge_anomaly_record(
    anomalies_by_emp: dict[str, dict[str, Any]],
    rec: dict[str, Any],
    issues: list[str],
    *,
    include_dates: bool = False,
) -> None:
    row: AttendanceRow = rec["row"]
    detail = _anomaly_detail(row, issues) if include_dates else None
    existing = anomalies_by_emp.get(rec["emp_id"])
    if existing is None:
        item = {
            "emp_id": rec["emp_id"],
            "name": rec["name"],
            "department": rec["department"],
            "shift_time": rec.get("shift_time", "") or "",
            "issues": list(issues),
        }
        if include_dates:
            item["dates"] = [row.date]
            item["details"] = [detail]
        anomalies_by_emp[rec["emp_id"]] = item
        return

    for issue in issues:
        if issue not in existing["issues"]:
            existing["issues"].append(issue)

    if include_dates:
        dates = existing.setdefault("dates", [])
        if row.date not in dates:
            dates.append(row.date)
        details = existing.setdefault("details", [])
        if detail is not None:
            detail_key = (detail["date"], detail["code"], detail["content"], detail["extra_content"])
            existing_keys = {
                (
                    existing_detail.get("date"),
                    existing_detail.get("code"),
                    existing_detail.get("content"),
                    existing_detail.get("extra_content"),
                )
                for existing_detail in details
            }
            if detail_key not in existing_keys:
                details.append(detail)


def detect_today_anomalies(
    year_month: str, target_date: str
) -> tuple[str, list[dict[str, Any]]]:
    """``target_date``의 근태 이상을 감지해 반환.

    알림은 이미 ERP에 산출된 지각/조퇴 시간과, 아직 코드/시간 값으로
    정리되지 않았지만 기준 시각을 벗어난 미처리 이상을 함께 보여준다.
    연차/휴가/훈련처럼 하루 전체 부재로 승인된 행은 제외한다.

    감지 규칙:
      - day_type == '평일' 또는 '평일2'만 대상. '평일2'는 주간 기준
        점심시간 30분 단축일로 17:30 퇴근을 정상 기준으로 본다.
      - 연차/월차/휴가/유급/공가 키워드가 day_type 또는 비고에 있으면
        전체 감지 대상에서 제외. 반차/반반차는 제외가 아니라 baseline
        만 이동(주간 한정)시켜 감지를 계속 수행한다.
      - shift_time이 ``SHIFT_BASELINES``에 없으면 감지 제외
        (빈 값 = 교대조 비번 날; 알 수 없는 근무형태).

    이상 항목:
      - 공제시간의 지각/조퇴/외출 값은 같은 근태코드가 있을 때만 정상
      - 반차/반반차/연차 등 휴가 코드에 지각/조퇴/외출 공제시간이 있으면
        "공제시간 불일치"
      - 기준 출근+grace 이후 check_in 없음 → "출근 누락"
      - 기준 퇴근+grace 이후 check_out 없음 → "퇴근 누락"
      - check_in 시각 > 기준 출근  AND late_hours == 0        → "지각 미처리"
      - check_out 시각 < 기준 퇴근 AND early_leave_hours == 0 → "조퇴 미처리"

    Raises ``MonthFileNotFound`` or ``FileLocked`` on I/O issues.
    """

    day_type = ""
    anomalies_by_emp: dict[str, dict[str, Any]] = {}
    reference = files._alert_reference_datetime()
    for path in files._month_file_paths_or_raise(year_month):
        for rec in parser._records_from_path(path):
            row: AttendanceRow = rec["row"]
            if row.date != target_date:
                continue
            if not day_type:
                day_type = row.day_type
            if row.day_type not in ALERT_WORKDAY_TYPES:
                continue
            if _is_full_day_leave(row.day_type, row.note, row.attendance_code):
                continue

            shift_time = rec.get("shift_time", "") or ""
            issues = _unprocessed_row_issues(row, shift_time, reference=reference)
            if not issues:
                continue

            _merge_anomaly_record(anomalies_by_emp, rec, issues)

    return day_type, list(anomalies_by_emp.values())


def detect_month_anomalies(year_month: str) -> list[dict[str, Any]]:
    """Return attendance anomalies for the selected month up to the current time."""

    anomalies_by_emp: dict[str, dict[str, Any]] = {}
    reference = files._alert_reference_datetime()
    for path in files._month_file_paths_or_raise(year_month):
        for rec in parser._records_from_path(path):
            row: AttendanceRow = rec["row"]
            shift_time = rec.get("shift_time", "") or ""
            issues = _unprocessed_row_issues(row, shift_time, reference=reference)
            if not issues:
                continue
            _merge_anomaly_record(anomalies_by_emp, rec, issues, include_dates=True)

    items = list(anomalies_by_emp.values())
    for item in items:
        if "dates" in item:
            item["dates"] = sorted(item["dates"])
        if "details" in item:
            item["details"] = sorted(
                item["details"],
                key=lambda detail: (
                    str(detail.get("date", "")),
                    str(detail.get("code", "")),
                    str(detail.get("content", "")),
                ),
            )
    return items
