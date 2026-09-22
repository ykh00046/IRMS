"""근태허가원 적재·대조 — docs/attendance-approvals.md §6 (BRM 몫) 검증.

  1. 적재 멱등(같은 배치 두 번 = 갱신 0) · 수정본(doc_hash) 갱신 · 필수 누락 거절 목록
  2. 대조 세 목록의 계산 · 동명이인 미매칭
  3. 수집 회차: 설정 없음 · 성공 · 로그인 실패 · 수집 실패 · 겹침 잠금 · 감사 한 줄
  4. 개인정보: 공개(트레이) 응답에 종류·사유가 실리지 않는다. 자격증명은 어디에도 없다

포털 자체는 부르지 않는다 — 가짜 클라이언트를 넣는다(HTTP 세 단계의 모양은
tests/test_portal_approvals.py 가 지킨다). 엑셀도 저장소에 없으므로 명단/월 행은
attendance_excel 헬퍼를 patch 해 주입한다.
"""

from __future__ import annotations

import importlib
import json
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.services import attendance_approvals as service

ADMIN_URL = "/api/attendance/admin/approvals"
COLLECT_URL = "/api/attendance/admin/approvals/collect"


def _reload_app():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod


def _tag() -> str:
    return uuid.uuid4().hex[:8].upper()


def _item(doc_no: str, **over: Any) -> dict[str, Any]:
    payload = {
        "doc_no": doc_no,
        "emp_name": "홍길동",
        "emp_id": "900033",
        "kind": "반차",
        "half": "오전",
        "start_date": "2026-09-25",
        "end_date": "2026-09-25",
        "status": "완료",
        "drafted_at": "2026-09-20",
        "title_raw": f"근태허가원 홍길동 2026-09-25 반차(오전) {doc_no}",
        "doc_hash": "sha256:aaa",
    }
    payload.update(over)
    return payload


def _store(items, *, collected_at="2026-09-22T01:05:00Z") -> dict[str, Any]:
    """적재 한 번(수집 경로와 같은 함수). 커밋까지 한다."""
    from src.db import get_connection

    with get_connection() as connection:
        result = service.upsert_batch(
            connection, items=items, collected_at=collected_at
        )
        connection.commit()
    return result


def _login_admin(client):
    client.get("/api/blend/records")  # csrf 쿠키 확보
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _csrf(client) -> dict[str, str]:
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _fetch(doc_no: str) -> dict[str, Any] | None:
    from src.db import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM attendance_approvals WHERE doc_no = ?", (doc_no,)
        ).fetchone()
    return None if row is None else {key: row[key] for key in row.keys()}


@pytest.fixture(autouse=True)
def _no_portal_credentials(monkeypatch):
    """기본은 '설정 안 됨' — 어떤 테스트도 실수로 실제 포털을 부르지 않는다."""
    import src.config as cfg

    monkeypatch.setattr(cfg, "PORTAL_USERNAME", "", raising=False)
    monkeypatch.setattr(cfg, "PORTAL_PASSWORD", "", raising=False)


def _configure_portal(monkeypatch):
    import src.config as cfg

    monkeypatch.setattr(cfg, "PORTAL_BASE_URL", "https://portal.example.test")
    monkeypatch.setattr(cfg, "PORTAL_USERNAME", "collector")
    monkeypatch.setattr(cfg, "PORTAL_PASSWORD", "secret")
    monkeypatch.setattr(cfg, "PORTAL_WINDOW_DAYS", 60)


def _clear_lock():
    from src.db import get_connection
    from src.services import settings_service

    with get_connection() as connection:
        settings_service.set_setting(connection, service.RUN_LOCK_KEY, "")
        connection.commit()


# ── 1. 적재(§3·§4) ──────────────────────────────────────────────────────────


def test_batch_is_idempotent_and_hash_change_updates():
    _reload_app()
    doc_no = f"AP-{_tag()}"

    first = _store([_item(doc_no)])
    assert first == {
        "received": 1, "created": 1, "updated": 0, "unchanged": 0, "rejected": [],
    }

    # 같은 배치 두 번 = 갱신 0.
    again = _store([_item(doc_no)])
    assert again == {
        "received": 1, "created": 0, "updated": 0, "unchanged": 1, "rejected": [],
    }

    # 수정본(해시 변경) → 갱신 1, 값도 실제로 바뀐다.
    revised = _store([_item(doc_no, doc_hash="sha256:bbb", half="오후", status="반송")])
    assert revised["updated"] == 1
    stored = _fetch(doc_no)
    assert stored["half"] == "오후"
    assert stored["status"] == "반송"
    assert stored["doc_hash"] == "sha256:bbb"
    assert stored["source"] == "portal"


def test_kind_is_normalized_and_raw_wording_is_kept():
    _reload_app()
    tag = _tag()
    items = [
        _item(f"K1-{tag}", kind="반반차"),
        _item(f"K2-{tag}", kind="오전 반차"),
        _item(f"K3-{tag}", kind="연차(하루)"),
        _item(f"K4-{tag}", kind="예비군 훈련"),
        # 실제 포털 문서의 종류 칸은 '훈련' 한 단어다(2026-09-22 실측).
        _item(f"K6-{tag}", kind="훈련"),
        _item(f"K5-{tag}", kind="포상휴가"),
    ]
    assert _store(items)["created"] == 6

    assert _fetch(f"K1-{tag}")["kind"] == "반반차"
    # "반반차"가 "반차"의 부분문자열 — 반반차를 먼저 봐야 한다.
    assert _fetch(f"K2-{tag}")["kind"] == "반차"
    assert _fetch(f"K3-{tag}")["kind"] == "연차"
    assert _fetch(f"K4-{tag}")["kind"] == "예비군"
    assert _fetch(f"K6-{tag}")["kind"] == "예비군", "종류 칸의 '훈련'도 예비군으로 접는다"
    other = _fetch(f"K5-{tag}")
    assert other["kind"] == "기타"
    assert other["kind_raw"] == "포상휴가", "모르는 표현도 원문은 남아야 한다"


def test_bad_items_are_rejected_without_failing_the_batch():
    _reload_app()
    tag = _tag()
    good = f"OK-{tag}"
    items = [
        _item(good),
        _item("", emp_name="아무개"),                        # 문서번호 없음
        _item(f"B1-{tag}", emp_name=""),                     # 이름 없음
        _item(f"B2-{tag}", kind=""),                         # 종류 없음
        _item(f"B3-{tag}", start_date=""),                   # 시작일 없음
        _item(f"B4-{tag}", start_date="2026/09/25"),         # 시작일 형식
        _item(f"B5-{tag}", end_date="2026-09-01"),           # 종료 < 시작
        "문자열-항목",                                        # 형식 오류
        _item(good),                                          # 같은 배치 중복
    ]
    body = _store(items)
    assert body["received"] == 9
    assert body["created"] == 1
    pairs = [(row["doc_no"], row["reason"]) for row in body["rejected"]]
    # 문서번호를 못 읽은 두 건(번호 공란·항목 형식)은 둘 다 doc_no "" 로 돌아온다.
    assert ("", "문서번호 없음") in pairs
    assert ("", "항목 형식 오류") in pairs
    reasons = dict(pairs)
    assert reasons[f"B1-{tag}"] == "대상자 이름 없음"
    assert reasons[f"B2-{tag}"] == "종류 없음"
    assert reasons[f"B3-{tag}"] == "시작일 없음"
    assert reasons[f"B4-{tag}"] == "시작일 형식 오류"
    assert reasons[f"B5-{tag}"] == "종료일이 시작일보다 앞섬"
    assert reasons[good] == "같은 배치에 문서번호 중복"
    assert len(body["rejected"]) == 8
    assert _fetch(good) is not None


def test_end_date_defaults_to_start_and_unknown_half_becomes_null():
    _reload_app()
    doc_no = f"D-{_tag()}"
    assert _store([_item(doc_no, end_date=None, half="AM")])["created"] == 1
    stored = _fetch(doc_no)
    assert stored["end_date"] == stored["start_date"] == "2026-09-25"
    assert stored["half"] is None


# ── 2. 대조 세 목록 + 동명이인(§4·§5) ───────────────────────────────────────

_ROSTER = [
    {"emp_id": "900033", "name": "홍길동", "department": "합성부", "factory": "1공장"},
    {"emp_id": "900044", "name": "김철수", "department": "배합부", "factory": "1공장"},
    {"emp_id": "900055", "name": "이영희", "department": "합성부", "factory": "1공장"},
    {"emp_id": "900066", "name": "이영희", "department": "배합부", "factory": "1공장"},
]


def _erp_row(emp_id: str, name: str, day: str, code: str = "", note: str = ""):
    return {
        "emp_id": emp_id,
        "name": name,
        "department": "합성부",
        "date": day,
        "day_type": "평일",
        "attendance_code": code,
        "note": note,
    }


def _month_view(items, erp_rows, *, month="2026-09"):
    """허가원을 넣고 대조 결과를 돌려준다(엑셀 대신 명단·행을 직접 주입)."""
    from src.db import get_connection

    with get_connection() as connection:
        service.upsert_batch(connection, items=items, collected_at="2026-09-22T01:05:00Z")
        connection.commit()
        return service.build_month_view(
            connection,
            month,
            roster=_ROSTER,
            erp_rows=erp_rows,
            now="2026-09-22T02:00:00Z",
        )


def test_the_three_reconciliation_lists():
    _reload_app()
    tag = _tag()
    items = [
        # 엑셀에도 휴가가 찍힌 정상 건.
        _item(f"R1-{tag}", emp_name="홍길동", emp_id="900033",
              kind="반차", start_date="2026-09-07", end_date="2026-09-07"),
        # 허가원만 있는 건(엑셀은 평일 근무, 휴가 표시 없음).
        _item(f"R2-{tag}", emp_name="김철수", emp_id="900044",
              kind="연차", half=None, start_date="2026-09-08", end_date="2026-09-08"),
    ]
    erp_rows = [
        _erp_row("900033", "홍길동", "2026-09-07", code="반차", note="오전"),
        _erp_row("900044", "김철수", "2026-09-08"),
        # 엑셀에만 있는 휴가(허가원 없음).
        _erp_row("900033", "홍길동", "2026-09-10", code="연차"),
        # 주휴는 대조에서 제외 — 그대로 세면 토·일이 전부 어긋난 건으로 뜬다.
        {**_erp_row("900044", "김철수", "2026-09-12", code="연차"), "day_type": "주휴"},
    ]
    view = _month_view(items, erp_rows)

    docs = {row["doc_no"] for row in view["items"]}
    assert {f"R1-{tag}", f"R2-{tag}"} <= docs

    only_approval = {row["doc_no"] for row in view["missing_in_erp"]}
    assert f"R2-{tag}" in only_approval
    assert f"R1-{tag}" not in only_approval

    only_erp = {(row["emp_id"], row["date"]) for row in view["missing_approval"]}
    assert ("900033", "2026-09-10") in only_erp
    assert ("900033", "2026-09-07") not in only_erp, "허가원이 덮은 날은 빠져야 한다"
    assert ("900044", "2026-09-12") not in only_erp, "주휴는 대조 대상이 아니다"

    assert view["erp_available"] is True
    assert view["collection"]["stale"] is False
    assert view["collection"]["last_collected_at"] == "2026-09-22T01:05:00Z"


def test_same_name_twice_stays_unmatched():
    _reload_app()
    tag = _tag()
    items = [
        # 사번 없이 이름만 — 명단에 이영희가 둘이라 아무에게도 붙이지 않는다.
        _item(f"N1-{tag}", emp_name="이영희", emp_id=None,
              start_date="2026-09-09", end_date="2026-09-09"),
        # 사번이 명단에 없고 이름도 없는 사람 — 미매칭(사유는 다르다).
        _item(f"N2-{tag}", emp_name="박무명", emp_id="999999",
              start_date="2026-09-09", end_date="2026-09-09"),
        # 이름만으로도 유일하면 맞춘다.
        _item(f"N3-{tag}", emp_name="김철수", emp_id=None,
              start_date="2026-09-09", end_date="2026-09-09"),
    ]
    view = _month_view(items, [])
    unmatched = {row["doc_no"]: row["reason"] for row in view["unmatched"]}
    assert unmatched[f"N1-{tag}"] == "동명이인"
    assert unmatched[f"N2-{tag}"] == "명단에 없음"
    assert f"N3-{tag}" not in unmatched

    matched = {row["doc_no"]: row for row in view["items"]}
    assert matched[f"N1-{tag}"]["matched_emp_id"] is None
    assert matched[f"N3-{tag}"]["matched_emp_id"] == "900044"
    assert matched[f"N3-{tag}"]["match_by"] == "이름"
    assert matched[f"N2-{tag}"]["unmatched_reason"] == "명단에 없음"


def test_missing_excel_keeps_the_list_and_skips_the_comparison():
    """엑셀을 못 읽은 달에도 목록은 보인다 — 한쪽이 없다고 화면이 비면 안 된다."""
    _reload_app()
    tag = _tag()
    view = _month_view([_item(f"E-{tag}")], [])
    assert view["erp_available"] is False
    assert {row["doc_no"] for row in view["items"]} >= {f"E-{tag}"}
    assert view["missing_in_erp"] == []
    assert view["missing_approval"] == []


def test_month_employee_rows_is_a_thin_read_only_helper():
    """대조가 쓰는 월 행 헬퍼 — 엑셀 파싱은 그대로 두고 결과만 납작하게 편다."""
    from src.services.attendance_excel import summary
    from src.services.attendance_excel.models import AttendanceRow

    def _row(day: str, code: str) -> AttendanceRow:
        return AttendanceRow(
            date=day, weekday="월", day_type="평일", check_in=None, check_out=None,
            next_day=False, weekday_early=0.0, weekday_normal=0.0,
            weekday_overtime=0.0, weekday_night=0.0, holiday_early=0.0,
            holiday_normal=0.0, holiday_overtime=0.0, holiday_night=0.0,
            late_hours=0.0, early_leave_hours=0.0, outing_hours=0.0,
            note="", attendance_code=code,
        )

    records = [
        {"emp_id": 120206.0, "name": "김정상", "department": "합성부",
         "row": _row("2026-09-07", "연차")},
        {"emp_id": "120206", "name": "김정상", "department": "합성부",
         "row": _row("2026-09-07", "연차")},   # 같은 행이 다른 소스 파일에 중복
        {"emp_id": "120209", "name": "김철수", "department": "배합부",
         "row": _row("2026-09-08", "")},
    ]
    with (
        patch.object(summary.files, "_month_file_paths_or_raise", return_value=["p1"]),
        patch.object(summary.parser, "_records_from_path", return_value=records),
    ):
        rows = summary.month_employee_rows("2026-09")

    assert len(rows) == 2, "같은 (사번·날짜·내용) 중복은 한 번만"
    assert rows[0]["emp_id"] == "120206"
    assert rows[0]["attendance_code"] == "연차"
    assert rows[1]["emp_id"] == "120209"


def test_stale_collection_is_flagged_after_two_days():
    assert service._is_stale("", now="2026-09-22T02:00:00Z") is True
    assert service._is_stale("2026-09-22T01:00:00Z", now="2026-09-22T02:00:00Z") is False
    assert service._is_stale("2026-09-19T20:00:00Z", now="2026-09-22T02:00:00Z") is True
    assert service.STALE_AFTER_DAYS == 2


def test_manager_read_endpoint_requires_a_manager():
    mainmod = _reload_app()
    client = TestClient(mainmod.app)
    denied = client.get(ADMIN_URL, params={"month": "2026-09"})
    assert denied.status_code in (401, 403), denied.text


def test_manager_endpoint_serves_the_month_view():
    mainmod = _reload_app()
    client = TestClient(mainmod.app)
    _login_admin(client)

    tag = _tag()
    _store(
        [
            _item(f"V1-{tag}", emp_name="김철수", emp_id="900044", kind="연차",
                  half=None, start_date="2026-09-08", end_date="2026-09-08")
        ]
    )

    from src.routers import attendance_routes

    with (
        patch.object(
            attendance_routes.excel_service, "employee_list", return_value=_ROSTER
        ),
        patch.object(
            attendance_routes.excel_service,
            "month_employee_rows",
            return_value=[_erp_row("900044", "김철수", "2026-09-08")],
        ),
        patch.object(
            attendance_routes.excel_service,
            "available_months",
            return_value=["2026-08", "2026-09"],
        ),
    ):
        res = client.get(ADMIN_URL, params={"month": "2026-09"})

    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["month"] == "2026-09"
    assert payload["available_months"] == ["2026-08", "2026-09"]
    assert {row["doc_no"] for row in payload["items"]} >= {f"V1-{tag}"}
    assert {row["doc_no"] for row in payload["missing_in_erp"]} >= {f"V1-{tag}"}
    assert payload["collection"]["total"] >= 1
    assert payload["collection"]["configured"] is False
    # 개인 사정은 여기서만 보인다.
    assert payload["items"][0]["kind"]


# ── 3. 수집 회차(§3) ────────────────────────────────────────────────────────


class _StubPortal:
    """포털 대신 미리 만든 HTML 을 돌려주는 가짜 클라이언트."""

    def __init__(self, list_html: str, bodies: dict[str, str]):
        self.list_html = list_html
        self.bodies = bodies
        self.list_calls = 0

    def fetch_list_page(self, *, start_date, end_date, page_index):
        self.list_calls += 1
        return self.list_html if page_index == 1 else ""

    def fetch_body(self, appr_id):
        from src.services.portal_approvals.client import PortalPermissionDenied

        if appr_id not in self.bodies:
            raise PortalPermissionDenied("PORTAL_DOC_FORBIDDEN")
        return self.bodies[appr_id]


def _stub_portal(tag: str) -> _StubPortal:
    from tests.test_portal_approvals import _body_html, _list_html, _page_rows

    rows = _page_rows(
        ("A1", f"20260907P{tag[:3]}-0040", "근태허가원/원료생산팀/박용재/반차/26.09.07"),
        ("AX", f"20260908P{tag[:3]}-0001", "근태허가원/다른팀/홍길동/연차/26.09.08"),
    )
    return _StubPortal(
        _list_html(rows),
        {
            "A1": _body_html(
                doc_no=f"20260907P{tag[:3]}-0040",
                drafted_at="2026-09-05",
                emp_name="박용재",
                emp_id="171013",
                period="26년09월07일 13시부터 ~ 26년09월07일17시30분까지(0.5일간)",
            )
        },
    )


def test_collect_is_off_when_credentials_are_missing():
    from src.db import get_connection

    _reload_app()
    with get_connection() as connection:
        result = service.collect_from_portal(connection)
    assert result["status"] == service.RUN_NOT_CONFIGURED
    with get_connection() as connection:
        assert service.collection_status(connection)["configured"] is False


def test_collect_stores_what_it_read_and_reports_what_it_could_not(monkeypatch):
    from src.db import get_connection

    _reload_app()
    _configure_portal(monkeypatch)
    _clear_lock()
    tag = _tag()
    portal = _stub_portal(tag)

    with get_connection() as connection:
        result = service.collect_from_portal(connection, client=portal)

    assert result["status"] == service.RUN_OK, result
    assert result["rows"] == 2
    assert result["created"] == 1
    assert result["forbidden"] == 1, "남의 부서 문서는 건너뛰고 센다"
    stored = _fetch(f"20260907P{tag[:3]}-0040")
    assert stored is not None
    assert stored["emp_name"] == "박용재"
    assert stored["emp_id"] == "171013"
    assert stored["kind"] == "반차"
    assert stored["half"] == "오후"
    assert stored["start_date"] == "2026-09-07"
    # 권한이 없던 문서는 제목에 이름·종류가 있어도 저장하지 않는다.
    assert _fetch(f"20260908P{tag[:3]}-0001") is None


def test_collect_twice_is_idempotent(monkeypatch):
    from src.db import get_connection

    _reload_app()
    _configure_portal(monkeypatch)
    tag = _tag()

    for expected_created, expected_unchanged in ((1, 0), (0, 1)):
        _clear_lock()
        with get_connection() as connection:
            result = service.collect_from_portal(connection, client=_stub_portal(tag))
        assert result["created"] == expected_created
        assert result["unchanged"] == expected_unchanged


def test_login_failure_is_reported_and_leaves_rows_alone(monkeypatch):
    from src.db import get_connection
    from src.services.portal_approvals.client import PortalLoginFailed

    _reload_app()
    _configure_portal(monkeypatch)
    _clear_lock()
    tag = _tag()
    doc_no = f"LF-{tag}"
    _store([_item(doc_no)])

    class _Dead:
        def fetch_list_page(self, **_kwargs):
            raise PortalLoginFailed("PORTAL_LOGIN_REJECTED")

        def fetch_body(self, _appr_id):
            raise AssertionError("본문까지 가면 안 된다")

    with get_connection() as connection:
        result = service.collect_from_portal(connection, client=_Dead())
    assert result["status"] == service.RUN_LOGIN_FAILED
    assert _fetch(doc_no) is not None, "수집 실패가 저장된 행을 건드렸다"
    with get_connection() as connection:
        assert service.last_run(connection)["status"] == service.RUN_LOGIN_FAILED


def test_any_other_failure_is_swallowed_and_recorded(monkeypatch):
    from src.db import get_connection

    _reload_app()
    _configure_portal(monkeypatch)
    _clear_lock()

    class _Broken:
        def fetch_list_page(self, **_kwargs):
            raise TimeoutError("포털 응답 없음")

        def fetch_body(self, _appr_id):
            raise AssertionError

    with get_connection() as connection:
        result = service.collect_from_portal(connection, client=_Broken())
    assert result["status"] == service.RUN_ERROR
    assert result["detail"] == "TimeoutError"


def test_a_second_run_while_one_is_going_is_refused(monkeypatch):
    from src.db import get_connection
    from src.db.time_utils import utc_now_text
    from src.services import settings_service

    _reload_app()
    _configure_portal(monkeypatch)
    with get_connection() as connection:
        settings_service.set_setting(
            connection, service.RUN_LOCK_KEY, utc_now_text()
        )
        connection.commit()

    class _Never:
        def fetch_list_page(self, **_kwargs):
            raise AssertionError("잠겨 있는데 포털을 불렀다")

        def fetch_body(self, _appr_id):
            raise AssertionError

    with get_connection() as connection:
        result = service.collect_from_portal(connection, client=_Never())
    assert result["status"] == service.RUN_BUSY
    _clear_lock()


def test_collect_endpoint_requires_a_manager_and_writes_one_audit_row(monkeypatch):
    from src.db import get_connection

    mainmod = _reload_app()
    client = TestClient(mainmod.app)
    denied = client.post(COLLECT_URL, json={})
    assert denied.status_code in (401, 403), denied.text

    _login_admin(client)
    _configure_portal(monkeypatch)
    _clear_lock()
    tag = _tag()

    def _audit_count() -> int:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM audit_logs "
                "WHERE action = 'attendance_approvals_collected'"
            ).fetchone()
        return int(row["n"])

    before = _audit_count()
    from src.routers import attendance_routes

    portal = _stub_portal(tag)
    real_collect = service.collect_from_portal
    with patch.object(
        attendance_routes.approvals_service,
        "collect_from_portal",
        side_effect=lambda connection, **_kw: real_collect(connection, client=portal),
    ):
        res = client.post(COLLECT_URL, json={}, headers=_csrf(client))

    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["status"] == service.RUN_OK
    assert payload["created"] == 1
    assert _audit_count() == before + 1

    with get_connection() as connection:
        row = connection.execute(
            "SELECT details_json, target_label FROM audit_logs "
            "WHERE action = 'attendance_approvals_collected' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    details = json.loads(row["details_json"])
    assert details["via"] == "portal"
    assert details["trigger"] == "manual"
    assert details["created"] == 1
    # 자격증명·개인 사정은 감사에 남기지 않는다.
    leaked = {"username", "password", "emp_name", "kind", "title_raw"} & set(details)
    assert not leaked, leaked
    assert "collector" not in row["target_label"]


# ── 4. 개인정보(§6) ─────────────────────────────────────────────────────────


def _keys_deep(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key))
            found |= _keys_deep(child)
    elif isinstance(value, list):
        for child in value:
            found |= _keys_deep(child)
    return found


def test_tray_alert_payload_has_no_approval_fields():
    from src.routers import public_attendance_alert_routes as alerts

    mainmod = _reload_app()
    client = TestClient(mainmod.app, client=("192.168.11.108", 50000))
    fake_items = [
        {
            "emp_id": "900033",
            "name": "홍길동",
            "department": "합성부",
            "shift_time": "주간",
            "issues": ["출근 누락"],
            "dates": ["2026-09-07"],
            "details": [],
        }
    ]
    with patch.object(
        alerts.excel_service, "detect_month_anomalies", return_value=fake_items
    ):
        res = client.get("/api/public/attendance-alerts/month")
    assert res.status_code == 200, res.text
    keys = _keys_deep(res.json())
    assert not (keys & {"kind", "kind_raw", "half", "approval", "approvals", "reason"})
    # 라우터가 결재 서비스를 아예 부르지 않는다(구조 계약).
    source = __import__("pathlib").Path(alerts.__file__).read_text(encoding="utf-8")
    assert "attendance_approvals" not in source


def test_no_public_intake_endpoint_remains():
    """수집 경로는 하나뿐이다 — 공개 수신 엔드포인트는 걷어냈다."""
    mainmod = _reload_app()
    paths = {getattr(route, "path", "") for route in mainmod.app.routes}
    assert "/api/public/attendance-approvals" not in paths
    client = TestClient(mainmod.app, client=("192.168.11.108", 50000))
    # GET 은 CSRF 검사를 거치지 않으므로 라우팅 결과(404)가 그대로 보인다.
    assert client.get("/api/public/attendance-approvals").status_code == 404
    # 접두 보호 목록에서도 빠졌다 — 이제 그냥 없는 경로다.
    from pathlib import Path

    import src.main as mainsrc

    assert "attendance-approvals" not in Path(mainsrc.__file__).read_text(encoding="utf-8")


def test_manager_payload_never_carries_credentials(monkeypatch):
    mainmod = _reload_app()
    client = TestClient(mainmod.app)
    _login_admin(client)
    _configure_portal(monkeypatch)

    from src.routers import attendance_routes

    with (
        patch.object(attendance_routes.excel_service, "employee_list", return_value=[]),
        patch.object(
            attendance_routes.excel_service, "month_employee_rows", return_value=[]
        ),
        patch.object(
            attendance_routes.excel_service, "available_months", return_value=["2026-09"]
        ),
    ):
        res = client.get(ADMIN_URL, params={"month": "2026-09"})
    assert res.status_code == 200, res.text
    body = res.text
    assert "collector" not in body and "secret" not in body
    collection = res.json()["collection"]
    assert collection["configured"] is True
    assert not ({"username", "password", "base_url"} & set(collection))
