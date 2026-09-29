"""오늘 휴무 공지 — 근태허가원에서 오늘 쉬는 사람을 골라 카톡 공지 문구를 만든다.

계약: ``docs/attendance-approvals.md`` §7. 발송은 ``tools/notify_today_leave.py`` 가 하고,
여기는 **고르기와 문구**만 한다(순수 로직 + 읽기 전용 조회).

원칙(2026-09-29 사용자 결정):
  1. 대상은 **BRM 근무자 명단(ERP 월 엑셀)에 있는 사람만**. 허가원이 있어도 명단에
     없으면 싣지 않는다 — 대조 화면(§4)과 같은 규칙이다. 이번 달 엑셀은 보통 월말에야
     올라오므로 명단은 ``alert_year_month()`` (이번 달, 없으면 가장 최근 달)를 쓴다.
  2. 종류는 문서에 적힌 그대로 모두 보인다(쉬는 것이니). 사유는 표에 없어 실리지 않는다.
  3. 근무조(ERP `근무조구분`)가 있으면 조별로 묶고, 아무도 조가 없으면 묶지 않는다.
  4. 쉬는 사람이 없으면 보내지 않는다 — 그래서 달력에 없는 공휴일에도 헛공지가 없다.
  5. 결재 취소는 반영하지 않는다(포털 목록에서 취소를 가릴 칸이 없다).
"""

import re
import sqlite3
from datetime import date
from typing import Any

from .attendance_approvals import _roster_indexes, _text, match_person, normalize_kind

WEEKDAY_KO = "월화수목금토일"

# 오전/오후를 붙이는 종류. 연차 등은 하루 전체라 붙일 것이 없다.
_HALF_KINDS = ("반차", "반반차")

# 근무조 표시 순서. `정상`(주간)을 먼저, 교대조는 A→B→C. 모르는 조는 뒤에 이름순.
_GROUP_ORDER = ("정상", "A조", "B조", "C조")


def todays_approvals(connection: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    """``day``(YYYY-MM-DD) 가 기간 안에 드는 허가원. 종류는 읽을 때 다시 접는다(§4)."""
    rows = connection.execute(
        """
        SELECT doc_no, emp_name, emp_id, kind_raw, kind, half, start_date, end_date
        FROM attendance_approvals
        WHERE start_date <= ? AND end_date >= ?
        ORDER BY start_date ASC, emp_name ASC, doc_no ASC
        """,
        (day, day),
    ).fetchall()
    items = [{key: row[key] for key in row.keys()} for row in rows]
    for item in items:
        raw = _text(item.get("kind_raw"))
        if raw:
            item["kind"] = normalize_kind(raw)
    return items


def roster_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ERP 월 엑셀 행(사람 × 날짜) → 사람 명단. 근무조는 **가장 늦은 날짜의 값**을 쓴다.

    한 달 사이에 조가 바뀌는 사람이 있다 — 오늘에 가장 가까운 값이 맞다.
    """
    people: dict[str, dict[str, Any]] = {}
    for rec in records:
        emp_id = _text(rec.get("emp_id"))
        if not emp_id:
            continue
        row = rec.get("row")
        stamp = _text(getattr(row, "date", "")) if row is not None else ""
        person = people.get(emp_id)
        if person is None:
            person = {
                "emp_id": emp_id,
                "name": _text(rec.get("name")),
                "department": _text(rec.get("department")),
                "shift_group": "",
                "_stamp": "",
            }
            people[emp_id] = person
        group = _text(rec.get("shift_group"))
        if group and stamp >= person["_stamp"]:
            person["shift_group"] = group
            person["_stamp"] = stamp
    for person in people.values():
        person.pop("_stamp", None)
    return list(people.values())


def load_roster() -> list[dict[str, Any]]:
    """BRM 근무자 명단 — 이번 달 엑셀, 없으면 가장 최근 달. 엑셀이 하나도 없으면 빈 목록."""
    from .attendance_excel import files, parser

    year_month = files.alert_year_month()
    records: list[dict[str, Any]] = []
    for path in files.month_file_paths(year_month):
        records.extend(parser._records_from_path(path))
    return roster_from_records(records)


def group_label(shift_group: str) -> str:
    """`A조(2교대)` → `A조`. 괄호 속 설명은 공지에 필요 없다."""
    return re.sub(r"\s*\(.*?\)\s*", "", shift_group or "").strip()


def kind_label(kind: str, half: str | None) -> str:
    if kind in _HALF_KINDS and half:
        return f"{kind}({half})"
    return kind


def todays_leaves(
    approvals: list[dict[str, Any]], roster: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """오늘 허가원을 명단에 맞춰 사람별로 합친다. 명단에 없거나 동명이인이면 뺀다.

    한 사람이 같은 날 문서가 둘이면(오전 반반차 + 오후 반차 등) 한 줄에 함께 적는다.
    """
    by_emp_id, by_name = _roster_indexes(roster)
    merged: dict[str, dict[str, Any]] = {}
    for approval in approvals:
        person, _how = match_person(approval, by_emp_id, by_name)
        if person is None:
            continue
        key = _text(person.get("emp_id")) or _text(person.get("name"))
        entry = merged.get(key)
        if entry is None:
            entry = {
                "name": _text(person.get("name")) or _text(approval.get("emp_name")),
                "department": _text(person.get("department")),
                "group": group_label(_text(person.get("shift_group"))),
                "kinds": [],
            }
            merged[key] = entry
        label = kind_label(_text(approval.get("kind")), approval.get("half"))
        if label and label not in entry["kinds"]:
            entry["kinds"].append(label)
    return list(merged.values())


def _group_sort_key(group: str) -> tuple[int, str]:
    if group in _GROUP_ORDER:
        return _GROUP_ORDER.index(group), ""
    return len(_GROUP_ORDER), group


def _person_line(entry: dict[str, Any]) -> str:
    line = f"· {entry['name']} {', '.join(entry['kinds'])}".rstrip()
    # 조색은 같은 팀 안의 별도 파트다 — 예전 휴가 공지와 같게 표시한다.
    if entry["department"].endswith("/조색"):
        line += " (조색)"
    return line


def build_message(leaves: list[dict[str, Any]], day: date) -> str | None:
    """공지 문구. 쉬는 사람이 없으면 None(보내지 않는다)."""
    if not leaves:
        return None
    header = f"🌴 오늘 휴무 ({day.month}/{day.day} {WEEKDAY_KO[day.weekday()]}) 총 {len(leaves)}명"
    ordered = sorted(leaves, key=lambda e: (_group_sort_key(e["group"]), e["name"]))
    if not any(entry["group"] for entry in ordered):
        return header + "\n\n" + "\n".join(_person_line(e) for e in ordered)
    blocks: list[str] = []
    current: str | None = None
    for entry in ordered:
        group = entry["group"] or "조 미상"
        if group != current:
            blocks.append(f"\n[{group}]")
            current = group
        blocks.append(_person_line(entry))
    return header + "\n" + "\n".join(blocks)


def message_for_day(connection: sqlite3.Connection, day: date) -> str | None:
    """오늘(``day``) 공지 문구 — 조회 + 명단 맞추기 + 문구. 없으면 None."""
    approvals = todays_approvals(connection, day.isoformat())
    if not approvals:
        return None
    return build_message(todays_leaves(approvals, load_roster()), day)
