"""근태허가원 수집 연계 — docs/attendance-approvals.md §6 (BRM 몫) 검증.

  1. 적재 멱등(같은 배치 두 번 = 갱신 0) · 수정본(doc_hash) 갱신 · 필수 누락 거절 목록
  2. 경계: 비사설 IP 403 · 운영 모드에서 토큰 없으면 403
  3. 대조 세 목록의 계산 · 동명이인 미매칭
  4. 개인정보: 공개(트레이) 응답에 종류·사유가 실리지 않는다

엑셀은 저장소에 없으므로 명단/월 행은 attendance_excel 헬퍼를 patch 해 주입한다
(파싱 자체는 test_attendance_excel_* 가 지킨다). 이 파일은 적재·대조·경계만 본다.
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.services import attendance_approvals as service

APPROVALS_URL = "/api/public/attendance-approvals"
ADMIN_URL = "/api/attendance/admin/approvals"


def _reload_app():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod


def _internal_client(mainmod) -> TestClient:
    # 사설 IP 위장 클라이언트 — InternalNetworkOnlyMiddleware 통과.
    return TestClient(mainmod.app, client=("192.168.11.108", 50000))


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


def _post(client, items, *, source="portal", collected_at="2026-09-22T01:05:00Z"):
    return client.post(
        APPROVALS_URL,
        json={"source": source, "collected_at": collected_at, "items": items},
    )


def _login_admin(client):
    client.get("/api/blend/records")  # csrf 쿠키 확보
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _fetch(doc_no: str) -> dict[str, Any] | None:
    from src.db import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM attendance_approvals WHERE doc_no = ?", (doc_no,)
        ).fetchone()
    return None if row is None else {key: row[key] for key in row.keys()}


# ── 1. 적재(§3) ─────────────────────────────────────────────────────────────


def test_batch_is_idempotent_and_hash_change_updates():
    mainmod = _reload_app()
    client = _internal_client(mainmod)
    doc_no = f"AP-{_tag()}"

    first = _post(client, [_item(doc_no)])
    assert first.status_code == 200, first.text
    assert first.json() == {
        "received": 1,
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "rejected": [],
    }

    # 같은 배치 두 번 = 갱신 0.
    again = _post(client, [_item(doc_no)])
    assert again.json() == {
        "received": 1,
        "created": 0,
        "updated": 0,
        "unchanged": 1,
        "rejected": [],
    }

    # 수정본(해시 변경) → 갱신 1, 값도 실제로 바뀐다.
    revised = _post(
        client,
        [_item(doc_no, doc_hash="sha256:bbb", half="오후", status="반송")],
    )
    assert revised.json()["updated"] == 1
    stored = _fetch(doc_no)
    assert stored["half"] == "오후"
    assert stored["status"] == "반송"
    assert stored["doc_hash"] == "sha256:bbb"
    # doc_no 는 멱등 키 — 행이 늘지 않았다.
    assert stored["source"] == "portal"


def test_kind_is_normalized_and_raw_wording_is_kept():
    mainmod = _reload_app()
    client = _internal_client(mainmod)
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
    assert _post(client, items).json()["created"] == 6

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
    mainmod = _reload_app()
    client = _internal_client(mainmod)
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
    res = _post(client, items)
    assert res.status_code == 200, res.text
    body = res.json()
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
    # 좋은 건은 실제로 들어갔다.
    assert _fetch(good) is not None


def test_end_date_defaults_to_start_and_unknown_half_becomes_null():
    mainmod = _reload_app()
    client = _internal_client(mainmod)
    doc_no = f"D-{_tag()}"
    assert _post(
        client, [_item(doc_no, end_date=None, half="AM")]
    ).json()["created"] == 1
    stored = _fetch(doc_no)
    assert stored["end_date"] == stored["start_date"] == "2026-09-25"
    assert stored["half"] is None


def test_over_two_hundred_items_is_422():
    mainmod = _reload_app()
    client = _internal_client(mainmod)
    tag = _tag()
    items = [_item(f"M-{tag}-{index}") for index in range(201)]
    res = _post(client, items)
    assert res.status_code == 422, res.text
    assert "TOO_MANY_ITEMS" in res.text
    # 한 건도 적재되지 않았다.
    assert _fetch(f"M-{tag}-0") is None
    assert len(items) == 201
    assert service.MAX_BATCH_ITEMS == 200


def test_run_writes_one_audit_entry_with_counts():
    from src.db import get_connection

    mainmod = _reload_app()
    client = _internal_client(mainmod)
    tag = _tag()
    res = _post(client, [_item(f"A1-{tag}"), _item(f"A2-{tag}"), _item("")])
    assert res.status_code == 200, res.text

    with get_connection() as connection:
        rows = connection.execute(
            "SELECT target_label, details_json FROM audit_logs "
            "WHERE action = 'attendance_approvals_collected' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchall()
    assert len(rows) == 1, "실행마다 한 줄(건마다 X)이어야 한다"
    import json

    details = json.loads(rows[0]["details_json"])
    assert details["received"] == 3
    assert details["created"] == 2
    assert details["rejected"] == 1
    # 개인 사정(이름·종류·문서번호)은 감사 상세에 담지 않는다.
    assert "emp_name" not in details and "kind" not in details
    assert details["reasons"] == ["문서번호 없음"]


# ── 2. 경계(§3) ─────────────────────────────────────────────────────────────


def test_non_private_ip_is_forbidden():
    mainmod = _reload_app()
    # TestClient 기본 호스트("testclient")는 유효한 IP 가 아니다 → 사설 아님.
    res = TestClient(mainmod.app).post(APPROVALS_URL, json={"items": []})
    assert res.status_code == 403
    assert res.json() == {"detail": "INTERNAL_NETWORK_ONLY"}


def test_production_requires_the_tray_token_even_from_loopback(monkeypatch):
    monkeypatch.setenv("IRMS_ENV", "production")
    monkeypatch.setenv("IRMS_REQUIRE_SESSION_SECRET", "false")
    monkeypatch.setenv("IRMS_SESSION_SECRET", "0" * 64)
    monkeypatch.setenv("IRMS_SEED_DEMO_DATA", "false")
    monkeypatch.setenv("IRMS_REQUIRE_TRAY_API_TOKEN", "true")
    monkeypatch.setenv("IRMS_TRAY_API_TOKEN", "test-tray-token")
    mainmod = _reload_app()

    client = TestClient(mainmod.app, client=("127.0.0.1", 50000))
    denied = client.post(APPROVALS_URL, json={"items": []})
    assert denied.status_code == 403
    assert denied.json() == {"detail": "TRAY_TOKEN_REQUIRED"}

    doc_no = f"P-{_tag()}"
    allowed = client.post(
        APPROVALS_URL,
        json={"source": "portal", "collected_at": "", "items": [_item(doc_no)]},
        headers={"X-IRMS-Tray-Token": "test-tray-token"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["created"] == 1


def test_manager_read_endpoint_requires_a_manager():
    mainmod = _reload_app()
    client = TestClient(mainmod.app)
    denied = client.get(ADMIN_URL, params={"month": "2026-09"})
    assert denied.status_code in (401, 403), denied.text


# ── 3. 대조 세 목록 + 동명이인(§4·§5) ───────────────────────────────────────

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
    # 숫자형 사번도 조회축(normalize_emp_id)으로 맞춰 나온다.
    assert rows[0]["emp_id"] == "120206"
    assert rows[0]["attendance_code"] == "연차"
    assert rows[1]["emp_id"] == "120209"


def test_stale_collection_is_flagged_after_two_days():
    assert service._is_stale("", now="2026-09-22T02:00:00Z") is True
    assert service._is_stale("2026-09-22T01:00:00Z", now="2026-09-22T02:00:00Z") is False
    assert service._is_stale("2026-09-19T20:00:00Z", now="2026-09-22T02:00:00Z") is True
    assert service.STALE_AFTER_DAYS == 2


def test_manager_endpoint_serves_the_month_view():
    mainmod = _reload_app()
    client = TestClient(mainmod.app)
    _login_admin(client)

    tag = _tag()
    internal = _internal_client(mainmod)
    assert _post(
        internal,
        [
            _item(f"V1-{tag}", emp_name="김철수", emp_id="900044", kind="연차",
                  half=None, start_date="2026-09-08", end_date="2026-09-08")
        ],
    ).status_code == 200

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
    # 개인 사정은 여기서만 보인다.
    assert payload["items"][0]["kind"]


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


def test_public_responses_never_carry_kind_or_reason_of_leave():
    """공개(트레이) 응답에 종류·사유가 실리지 않는다 — 공용 PC 배려(§1.3)."""
    mainmod = _reload_app()
    client = _internal_client(mainmod)

    # ① 수집 응답: 집계 + 거절 목록(문서번호 + 고정 사유)뿐이다.
    body = _post(client, [_item(f"PV-{_tag()}", kind="반차", half="오전")]).json()
    assert set(body) == {"received", "created", "updated", "unchanged", "rejected"}
    leaked = _keys_deep(body) & {
        "kind", "kind_raw", "half", "emp_name", "title_raw", "items", "status"
    }
    assert not leaked, f"공개 응답에 개인 사정이 실렸습니다: {sorted(leaked)}"
    # 거절 항목은 문서번호 + 고정 사유 두 칸뿐이다(이름·종류를 되돌려 싣지 않는다).
    rejected = _post(client, [_item("", emp_name="홍길동")]).json()["rejected"]
    assert [set(row) for row in rejected] == [{"doc_no", "reason"}]
    assert rejected[0]["reason"] == "문서번호 없음"


def test_tray_alert_payload_has_no_approval_fields():
    from src.routers import public_attendance_alert_routes as alerts

    mainmod = _reload_app()
    client = _internal_client(mainmod)
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
