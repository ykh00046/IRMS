"""오늘 휴무 공지 — docs/attendance-approvals.md §7 검증.

  1. 오늘 기간에 드는 허가원만 고른다(여러 날짜 문서 포함)
  2. 명단에 없는 사람·동명이인은 뺀다. 근무조는 가장 늦은 날짜의 값
  3. 문구: 조별 묶음·순서, 조가 아무도 없으면 묶지 않음, 오전/오후, 한 사람 두 문서
  4. 쉬는 사람이 없으면 None(보내지 않는다)

DB 는 표 하나만 만든 메모리 SQLite 를 쓴다. 엑셀은 명단 목록을 직접 넣는다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from types import SimpleNamespace

from src.services import leave_notice as notice

DAY = date(2026, 9, 30)  # 수요일


def _db(rows: list[tuple]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE attendance_approvals (
            doc_no TEXT, emp_name TEXT, emp_id TEXT, kind_raw TEXT, kind TEXT,
            half TEXT, start_date TEXT, end_date TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO attendance_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    return connection


def _person(emp_id, name, group="정상", dept="원료생산팀"):
    return {"emp_id": emp_id, "name": name, "shift_group": group, "department": dept}


def _approval(emp_id, name, kind, half=None):
    return {"emp_id": emp_id, "emp_name": name, "kind": kind, "half": half}


# ── 1. 오늘 고르기 ───────────────────────────────────────────────────────────


def test_todays_approvals_picks_documents_covering_the_day():
    connection = _db([
        ("D1", "홍길동", "1", "연차", "연차", None, "2026-09-30", "2026-09-30"),
        ("D2", "김철수", "2", "연차", "연차", None, "2026-09-28", "2026-10-02"),
        ("D3", "이영희", "3", "반차", "반차", "오후", "2026-09-29", "2026-09-29"),
        ("D4", "박민수", "4", "연차", "연차", None, "2026-10-01", "2026-10-01"),
    ])
    picked = notice.todays_approvals(connection, "2026-09-30")
    assert sorted(a["doc_no"] for a in picked) == ["D1", "D2"]


def test_todays_approvals_refolds_kind_from_raw():
    connection = _db([
        ("D1", "홍길동", "1", "오후반차", "기타", "오후", "2026-09-30", "2026-09-30"),
    ])
    assert notice.todays_approvals(connection, "2026-09-30")[0]["kind"] == "반차"


# ── 2. 명단 맞추기 ───────────────────────────────────────────────────────────


def test_people_outside_roster_and_same_name_are_left_out():
    roster = [
        _person("1", "홍길동"),
        _person("2", "김민호", "A조"),
        _person("3", "김민호", "B조"),
    ]
    approvals = [
        _approval("1", "홍길동", "연차"),
        _approval("", "김민호", "연차"),  # 사번 없음 + 동명이인 → 뺀다
        _approval("9", "외부인", "연차"),  # 명단에 없음 → 뺀다
    ]
    leaves = notice.todays_leaves(approvals, roster)
    assert [e["name"] for e in leaves] == ["홍길동"]


def test_same_name_is_matched_when_emp_id_is_present():
    roster = [_person("2", "김민호", "A조"), _person("3", "김민호", "B조")]
    leaves = notice.todays_leaves([_approval("3", "김민호", "연차")], roster)
    assert leaves[0]["group"] == "B조"


def test_roster_takes_the_latest_shift_group():
    def rec(emp_id, day, group):
        return {
            "emp_id": emp_id, "name": "홍길동", "department": "원료생산팀",
            "shift_group": group, "row": SimpleNamespace(date=day),
        }

    roster = notice.roster_from_records([
        rec("1", "2026-06-02", "A조(2교대)"),
        rec("1", "2026-06-20", "C조(2교대)"),
        rec("1", "2026-06-10", "B조(2교대)"),
        rec("1", "2026-06-25", ""),
    ])
    assert roster == [{
        "emp_id": "1", "name": "홍길동", "department": "원료생산팀", "shift_group": "C조(2교대)",
    }]


def test_one_person_with_two_documents_is_one_line():
    roster = [_person("1", "홍길동")]
    leaves = notice.todays_leaves(
        [_approval("1", "홍길동", "반반차", "오전"), _approval("1", "홍길동", "반차", "오후")],
        roster,
    )
    assert len(leaves) == 1
    assert leaves[0]["kinds"] == ["반반차(오전)", "반차(오후)"]


# ── 3. 문구 ──────────────────────────────────────────────────────────────────


def test_message_groups_by_shift_in_fixed_order():
    roster = [
        _person("1", "홍길동", "C조(2교대)"),
        _person("2", "김철수", "정상"),
        _person("3", "이영희", "A조(2교대)", "원료생산팀/조색"),
    ]
    approvals = [
        _approval("1", "홍길동", "연차"),
        _approval("2", "김철수", "반차", "오후"),
        _approval("3", "이영희", "병가"),
    ]
    message = notice.build_message(notice.todays_leaves(approvals, roster), DAY)
    assert message == (
        "🌴 오늘 휴무 (9/30 수) 총 3명\n"
        "\n[정상]\n"
        "· 김철수 반차(오후)\n"
        "\n[A조]\n"
        "· 이영희 병가 (조색)\n"
        "\n[C조]\n"
        "· 홍길동 연차"
    )


def test_message_is_flat_when_nobody_has_a_group():
    roster = [_person("1", "홍길동", ""), _person("2", "김철수", "")]
    approvals = [_approval("1", "홍길동", "연차"), _approval("2", "김철수", "연차")]
    message = notice.build_message(notice.todays_leaves(approvals, roster), DAY)
    assert message == "🌴 오늘 휴무 (9/30 수) 총 2명\n\n· 김철수 연차\n· 홍길동 연차"


def test_missing_group_is_shown_as_unknown_when_others_have_one():
    roster = [_person("1", "홍길동", ""), _person("2", "김철수", "B조(2교대)")]
    approvals = [_approval("1", "홍길동", "연차"), _approval("2", "김철수", "연차")]
    message = notice.build_message(notice.todays_leaves(approvals, roster), DAY)
    assert message.endswith("[B조]\n· 김철수 연차\n\n[조 미상]\n· 홍길동 연차")


def test_half_is_shown_only_for_half_day_kinds():
    assert notice.kind_label("반차", "오전") == "반차(오전)"
    assert notice.kind_label("반반차", None) == "반반차"
    assert notice.kind_label("연차", "오전") == "연차"


# ── 4. 보내지 않는 날 ────────────────────────────────────────────────────────


def test_nobody_off_means_no_message():
    assert notice.build_message([], DAY) is None


def test_message_for_day_without_documents_skips_roster(monkeypatch):
    def boom():
        raise AssertionError("허가원이 없으면 엑셀을 읽지 않는다")

    monkeypatch.setattr(notice, "load_roster", boom)
    assert notice.message_for_day(_db([]), DAY) is None
