"""근태 화면 재설계 서버 계약(2026-08-14) 검증.

  1. GET /attendance/admin/anomalies — 책임자 전용 월 이상 목록(트레이 팝업의 웹 본판).
  2. /me 월 파일 없음 404 가 available_months 를 실어 준다(월초 막다른 골목 해소).
  3. 엑셀 파싱 캐시 — 같은 파일은 재파싱하지 않고, 파일이 바뀌면 무효화된다.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login_admin(client):
    client.get("/api/blend/records")
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


# ── 1. 관리자 이상 목록 ─────────────────────────────────────────────────────


def test_admin_anomalies_requires_manager_and_returns_items():
    from src.routers import attendance_routes

    client = _client()
    # 비로그인 → 거부.
    denied = client.get("/api/attendance/admin/anomalies")
    assert denied.status_code in (401, 403), denied.text

    _login_admin(client)
    fake_items = [{
        "emp_id": "250612", "name": "홍길동", "department": "생산",
        "shift_time": "주간", "issues": ["근태코드 누락(지각)"],
        "dates": ["2026-08-07"],
        "details": [{
            "date": "2026-08-07", "display_date": "08-07", "code": "0",
            "content": "근태 이상", "extra_content": "", "status": "",
            "issues": ["근태코드 누락(지각)"],
        }],
    }]
    with (
        patch.object(
            attendance_routes.excel_service, "detect_month_anomalies",
            return_value=fake_items,
        ),
        patch.object(
            attendance_routes.excel_service, "available_months",
            return_value=["2026-07", "2026-08"],
        ),
    ):
        res = client.get(
            "/api/attendance/admin/anomalies", params={"month": "2026-08"}
        )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["month"] == "2026-08"
    assert data["total"] == 1
    assert data["detail_total"] == 1
    # 판정 원문 사유가 그대로 실린다 — 팝업의 축약 표기와 달리 화면은 원문을 쓴다.
    assert data["items"][0]["details"][0]["issues"] == ["근태코드 누락(지각)"]
    assert data["available_months"] == ["2026-07", "2026-08"]


# ── 2. 월 파일 없음 폴백 ────────────────────────────────────────────────────


def test_month_not_found_detail_carries_available_months():
    from src.routers import attendance_routes
    from src.services import attendance_excel as excel_service

    with (
        patch.object(
            attendance_routes.excel_service, "load_month_for_employee",
            side_effect=excel_service.MonthFileNotFound("2026-09"),
        ),
        patch.object(
            attendance_routes.excel_service, "available_months",
            return_value=["2026-07", "2026-08"],
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            attendance_routes._load_attendance_response("2026-09", "250612")
    assert exc.value.status_code == 404
    detail = exc.value.detail
    assert detail["code"] == "MONTH_FILE_NOT_FOUND"
    assert detail["requested_month"] == "2026-09"
    assert detail["available_months"] == ["2026-07", "2026-08"]


# ── 3. 파싱 캐시 ────────────────────────────────────────────────────────────


class _FakeWb:
    sheetnames = ["Sheet1"]
    active = "ws"

    def __getitem__(self, _name):
        return "ws"

    def close(self):
        return None


def test_parser_cache_hits_and_invalidates(tmp_path):
    from src.services.attendance_excel import parser

    f: Path = tmp_path / "monthly_attendance_2026-08.xlsx"
    f.write_bytes(b"v1-content")

    calls = {"n": 0}

    def fake_load(_path):
        calls["n"] += 1
        return _FakeWb()

    rec = {"emp_id": "1", "name": "a", "department": "d", "row": object()}
    with (
        patch.object(parser, "_load_workbook", side_effect=fake_load),
        patch.object(parser, "_column_map_from_ws", return_value={}),
        patch.object(parser, "_iter_data_rows", return_value=[object()]),
        patch.object(parser, "_row_to_record", return_value=rec),
    ):
        first = parser._records_from_path(f)
        second = parser._records_from_path(f)   # 같은 파일 → 캐시, 재파싱 없음
        assert calls["n"] == 1
        assert second is first

        f.write_bytes(b"v2-content-longer")     # 파일 갱신 → 무효화
        with patch.object(parser, "_iter_data_rows", return_value=[object()]):
            parser._records_from_path(f)
        assert calls["n"] == 2


# ── 4. 임시 비밀번호 발급 응답 ──────────────────────────────────────────────
# 발급 결과 카드가 "홍길동 (사번 171013)" 로 누구 것인지 못 박으려면 서버가 이름을
# 함께 줘야 한다. 사번만 돌려주면 옆자리 사람에게 잘못 불러줘도 알 길이 없다
# (2026-08-28 신입 첫 로그인 동선 정리).
def test_reset_password_response_carries_employee_label():
    from src.routers import attendance_routes
    from src.services.attendance_excel.models import AttendanceProfile

    client = _client()
    _login_admin(client)

    profile = AttendanceProfile(
        emp_id="171013", name="홍길동", department="생산", factory="1공장",
        shift_time="주간", shift_group="A", job_type="정규", gender="남",
    )
    with (
        patch.object(
            attendance_routes.attendance_auth, "reset_password_to_temporary",
            return_value="K7WX4M2P",
        ),
        patch.object(
            attendance_routes.excel_service, "employee_profile_from_any_month",
            return_value=profile,
        ),
    ):
        token = client.cookies.get("csrftoken")
        res = client.post(
            "/api/attendance/admin/reset-password",
            json={"emp_id": "171013"},
            headers={"x-csrftoken": token} if token else {},
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["temporary_password"] == "K7WX4M2P"
    assert body["employee_label"] == "홍길동 (사번 171013)"
    assert body["password_reset_required"] is True


# ── 4. 임시 비밀번호 상태의 비밀번호 변경(2026-08-28 결정 B1) ─────────────────
# 방금 임시 비밀번호로 로그인한 직원에게 그 임시 비밀번호를 한 번 더 타이핑하게 하지
# 않는다. 다만 '현재 비밀번호 없이 바꾸기'는 DB 가 실제로 초기화 상태일 때만 열린다 —
# 낡은 세션 플래그로 남의 비밀번호를 갈아치우는 길이 되면 안 된다.


def _csrf_headers(client):
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _new_emp_id() -> str:
    import uuid

    return "T" + uuid.uuid4().hex[:7]


def _login_employee(client, emp_id: str, password: str):
    client.get("/attendance/login")  # csrf 쿠키 확보
    res = client.post(
        "/api/attendance/login",
        json={"emp_id": emp_id, "password": password},
        headers=_csrf_headers(client),
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_reset_flow_changes_password_without_the_current_one():
    from src import attendance_auth

    client = _client()
    emp = _new_emp_id()
    attendance_auth._create(emp, "TempPw49", reset_required=1)

    payload = _login_employee(client, emp, "TempPw49")
    assert payload["password_reset_required"] is True

    res = client.post(
        "/api/attendance/change-password",
        json={"new_password": "Strong-pw-9"},
        headers=_csrf_headers(client),
    )
    assert res.status_code == 200, res.text

    status_res = client.get("/api/attendance/session")
    assert status_res.status_code == 200, status_res.text
    assert status_res.json()["password_reset_required"] is False

    client.post("/api/attendance/logout", headers=_csrf_headers(client))
    relogin = client.post(
        "/api/attendance/login",
        json={"emp_id": emp, "password": "Strong-pw-9"},
        headers=_csrf_headers(client),
    )
    assert relogin.status_code == 200, relogin.text
    assert relogin.json()["password_reset_required"] is False


def test_normal_session_still_requires_the_current_password():
    """초기화 상태가 아닌 사람이 현재 비밀번호를 비우면 종전대로 거부된다."""
    from src import attendance_auth

    client = _client()
    emp = _new_emp_id()
    attendance_auth._create(emp, "Owner-pw-7", reset_required=0)
    _login_employee(client, emp, "Owner-pw-7")

    res = client.post(
        "/api/attendance/change-password",
        json={"new_password": "Another-pw-8"},
        headers=_csrf_headers(client),
    )
    assert res.status_code == 400, res.text
    assert "CURRENT_PASSWORD_WRONG" in res.text
    # 비밀번호는 그대로여야 한다.
    record = attendance_auth._fetch(emp)
    from src.security import verify_password

    assert verify_password("Owner-pw-7", record["password_hash"])


def test_stale_reset_session_cannot_skip_the_current_password():
    """세션엔 '초기화 필요'가 남아 있지만 DB 는 이미 본인 비밀번호 — 우회 금지."""
    from src import attendance_auth

    client = _client()
    emp = _new_emp_id()
    attendance_auth._create(emp, "TempPw50", reset_required=1)
    _login_employee(client, emp, "TempPw50")

    # 다른 경로로 이미 비밀번호가 확정된 상황을 재현한다(세션 플래그는 그대로).
    attendance_auth._set_password(emp, "Owner-pw-6", reset_required=0)

    res = client.post(
        "/api/attendance/change-password",
        json={"new_password": "Hijack-pw-5"},
        headers=_csrf_headers(client),
    )
    assert res.status_code == 400, res.text
    assert "CURRENT_PASSWORD_REQUIRED" in res.text
    from src.security import verify_password

    assert verify_password("Owner-pw-6", attendance_auth._fetch(emp)["password_hash"])


def test_reset_flow_still_enforces_password_strength():
    """현재 비밀번호를 묻지 않을 뿐, 새 비밀번호 규칙은 그대로다."""
    from src import attendance_auth

    client = _client()
    emp = _new_emp_id()
    attendance_auth._create(emp, "TempPw51", reset_required=1)
    _login_employee(client, emp, "TempPw51")

    res = client.post(
        "/api/attendance/change-password",
        json={"new_password": "12345678"},
        headers=_csrf_headers(client),
    )
    assert res.status_code == 400, res.text
    assert "PASSWORD_SEQUENTIAL_DIGITS" in res.text
    # 8자 미만은 본문 모델(min_length)이 먼저 막는다 — 422.
    short = client.post(
        "/api/attendance/change-password",
        json={"new_password": "short1"},
        headers=_csrf_headers(client),
    )
    assert short.status_code == 422, short.text
    # 아무것도 바뀌지 않았다.
    assert attendance_auth._fetch(emp)["password_reset_required"] == 1


def test_reset_flow_writes_an_audit_entry():
    from src import attendance_auth
    from src.db import get_connection

    client = _client()
    emp = _new_emp_id()
    attendance_auth._create(emp, "TempPw52", reset_required=1)
    _login_employee(client, emp, "TempPw52")

    res = client.post(
        "/api/attendance/change-password",
        json={"new_password": "Fresh-pw-42"},
        headers=_csrf_headers(client),
    )
    assert res.status_code == 200, res.text

    with get_connection() as connection:
        row = connection.execute(
            "SELECT action, target_id FROM audit_logs "
            "WHERE action = 'attendance_password_set_after_reset' AND target_id = ? ",
            (emp,),
        ).fetchone()
    assert row is not None, "초기화 경로 비밀번호 설정이 감사 로그에 남지 않았다"
