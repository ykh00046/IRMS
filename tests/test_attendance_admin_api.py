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
