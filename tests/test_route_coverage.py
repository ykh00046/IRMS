"""쓰기 라우트 커버리지 보강 — 로드맵 Phase 2 (P2-1).

전체 검토(2026-07-07)에서 라우트 테스트가 없다고 확인된 엔드포인트:
근태 로그인/비번변경(422 사고 이력 파일!), 점도 측정 등록/삭제,
배합 일괄 생성·삭제(소프트/하드), 서명/시트백업 설정 권한 게이트.
"""

from __future__ import annotations

import importlib
import uuid


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _admin_login(client):
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    return _csrf(client)


# ── 근태 라우트 (future annotations 422 사고 이력 — 회귀 방지 최우선) ──────────

def test_attendance_login_valid_body_never_422():
    """정상 JSON 본문은 무슨 일이 있어도 422 가 아니어야 한다.

    2026-07-01 사고: `from __future__ import annotations` + @limiter.limit 조합이
    본문 모델 해석을 깨 모든 로그인이 422 로 실패. 이 테스트가 그 회귀를 잡는다.
    """
    client = _client()
    client.get("/attendance/login")  # csrf 쿠키 확보
    res = client.post(
        "/api/attendance/login",
        json={"emp_id": "000000", "password": "nope1234"},
        headers=_csrf(client),
    )
    assert res.status_code == 401, res.text  # 미프로비저닝 → 401 (422 면 회귀!)
    assert "INVALID_CREDENTIALS" in res.text


def test_attendance_login_and_change_password_flow():
    """프로비저닝된 계정: 로그인 → 비번 변경 → 새 비번 재로그인 (라우트 계층)."""
    from src import attendance_auth

    client = _client()
    emp = "T" + uuid.uuid4().hex[:7]
    attendance_auth._create(emp, "oldpw1x9", reset_required=0)

    client.get("/attendance/login")
    headers = _csrf(client)

    # 틀린 비번 → 401 (422 아님)
    bad = client.post("/api/attendance/login",
                      json={"emp_id": emp, "password": "wrong123"}, headers=headers)
    assert bad.status_code == 401

    # 로그인 성공
    ok = client.post("/api/attendance/login",
                     json={"emp_id": emp, "password": "oldpw1x9"}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["emp_id"] == emp
    headers = _csrf(client)

    # 비번 변경: 현재 비번 틀리면 거부
    res = client.post("/api/attendance/change-password",
                      json={"current_password": "wrong123", "new_password": "newpw2y8"},
                      headers=headers)
    assert res.status_code in (400, 401)

    # 정상 변경 → 새 비번으로 재로그인
    res = client.post("/api/attendance/change-password",
                      json={"current_password": "oldpw1x9", "new_password": "newpw2y8"},
                      headers=headers)
    assert res.status_code == 200, res.text
    client.post("/api/attendance/logout", headers=headers)
    relogin = client.post("/api/attendance/login",
                          json={"emp_id": emp, "password": "newpw2y8"}, headers=headers)
    assert relogin.status_code == 200


# ── 점도 측정 등록/삭제 라우트 ────────────────────────────────────────────────

def test_viscosity_reading_add_and_delete_routes():
    client = _client()
    headers = _admin_login(client)
    prod = "VR" + uuid.uuid4().hex[:6].upper()

    # 점도 제품은 레시피 제품명과 연동 — 레시피 먼저 등록
    raw = f"반제품명\t원료A\t원료B\n{prod}\t60\t40"
    assert client.post("/api/recipes/import", json={"raw_text": raw, "force": True},
                       headers=headers).status_code == 200
    res = client.post("/api/viscosity/products",
                      json={"code": prod, "name": prod}, headers=headers)
    assert res.status_code == 200, res.text
    pid = res.json()["id"]

    # 등록
    lot = f"{prod}26070701"
    res = client.post("/api/viscosity/readings",
                      json={"product_id": pid, "lot_no": lot, "viscosity": 1234.5},
                      headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["new_reading"]["lot_no"] == lot

    from src.db import get_connection
    with get_connection() as conn:
        rid = conn.execute(
            "SELECT id FROM viscosity_readings WHERE product_id=? AND lot_no=?",
            (pid, lot),
        ).fetchone()["id"]

    # 삭제 → 실제 제거 확인
    res = client.request("DELETE", f"/api/viscosity/readings/{rid}", headers=headers)
    assert res.status_code == 200 and res.json()["deleted"] == rid
    with get_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM viscosity_readings WHERE id=?", (rid,)
        ).fetchone() is None
    # 없는 id 는 404
    assert client.request("DELETE", f"/api/viscosity/readings/{rid}",
                          headers=headers).status_code == 404


# ── 배합 일괄 생성 · 삭제(소프트/하드) 라우트 ────────────────────────────────

def test_blend_bulk_and_delete_routes():
    client = _client()
    headers = _admin_login(client)
    prod = "BK" + uuid.uuid4().hex[:6].upper()
    worker = "일괄작업" + uuid.uuid4().hex[:6]

    raw = f"반제품명\t원료A\t원료B\n{prod}\t60\t40"
    assert client.post("/api/recipes/import", json={"raw_text": raw, "force": True},
                       headers=headers).status_code == 200
    rid = client.get("/api/blend/recipes").json()["items"]
    recipe_id = next(r["id"] for r in rid if r["product_name"] == prod)

    # 일괄 생성은 작업자 세션 필요 — 없으면 401
    body = {"recipe_id": recipe_id, "worker": worker,
            "entries": [{"work_date": "2026-07-05", "total_amount": 100},
                        {"work_date": "2026-07-06", "total_amount": 200}]}
    assert client.post("/api/blend/records/bulk", json=body,
                       headers=headers).status_code == 401

    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    res = client.post("/api/blend/records/bulk", json=body, headers=headers)
    assert res.status_code == 200, res.text
    ids = res.json()["ids"] if "ids" in res.json() else res.json().get("created_ids")
    assert ids and len(ids) == 2

    # 소프트 취소 → status=canceled, 목록에서 제외
    res = client.request("DELETE", f"/api/blend/records/{ids[0]}", headers=headers)
    assert res.status_code == 200
    from src.db import get_connection
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM blend_records WHERE id=?",
                            (ids[0],)).fetchone()["status"] == "canceled"

    # 하드 삭제는 책임자 전용 — admin 세션이 이미 있으므로 200, 행 제거
    res = client.request("DELETE", f"/api/blend/records/{ids[1]}?hard=1", headers=headers)
    assert res.status_code == 200
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM blend_records WHERE id=?",
                            (ids[1],)).fetchone() is None


def test_blend_manual_entry_flag_route():
    """POST /blend/records manual_entry → 기록에 저장·조회 반영, 감사 로그 기록."""
    client = _client()
    headers = _admin_login(client)
    worker = "수동작업" + uuid.uuid4().hex[:6]
    prod = "MAN" + uuid.uuid4().hex[:5].upper()
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-09",
        "total_amount": 100, "manual_entry": True,
        "details": [{"material_name": "A", "ratio": 60,
                     "theory_amount": 60, "actual_amount": 60,
                     "manual_entry": True},
                    {"material_name": "B", "ratio": 40,
                     "theory_amount": 40, "actual_amount": 40}],
    }, headers=headers)
    assert created.status_code == 200, created.text
    assert created.json()["manual_entry"] is True

    rid = created.json()["id"]
    detail = client.get(f"/api/blend/records/{rid}").json()
    assert detail["manual_entry"] is True
    # 행 단위: 손입력한 A 만 True
    flags = {d["material_name"]: d["manual_entry"] for d in detail["details"]}
    assert flags == {"A": True, "B": False}

    # manual_entry 생략 시 기본 False
    c2 = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-09",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100,
                     "theory_amount": 100, "actual_amount": 100}],
    }, headers=headers)
    assert c2.json()["manual_entry"] is False


def test_blend_hard_delete_requires_manager():
    client = _client()
    headers = _admin_login(client)
    prod = "HD" + uuid.uuid4().hex[:6].upper()
    worker = "삭제작업" + uuid.uuid4().hex[:6]
    raw = f"반제품명\t원료A\n{prod}\t100"
    client.post("/api/recipes/import", json={"raw_text": raw, "force": True}, headers=headers)
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/auth/logout", headers=headers)  # 관리 세션 종료

    client.get("/api/blend/records")
    headers = _csrf(client)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-05",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100,
                     "theory_amount": 100, "actual_amount": 100}],
    }, headers=headers)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    # 배합 세션만으로 하드 삭제 시도 → 403
    res = client.request("DELETE", f"/api/blend/records/{rid}?hard=1", headers=headers)
    assert res.status_code == 403


# ── 세션 공존: 책임자 로그인/로그아웃이 배합 작업자 세션을 끊지 않는다 ────────

def test_manager_login_preserves_blend_worker_session():
    """배합 작업 중 책임자 화면을 다녀와도(로그인·로그아웃) 작업자 세션 유지.

    같은 쿠키 세션을 공유하는 구조에서 session.clear() 가 현장 세션까지 지워
    '배합으로 돌아가면 재로그인 요구' 증상이 있었다(2026-07-08)."""
    client = _client()
    worker = "세션유지" + uuid.uuid4().hex[:6]

    client.get("/api/blend/records")
    headers = _csrf(client)
    client.post("/api/workers", json={"name": worker}, headers=headers)
    res = client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    assert res.status_code == 200
    assert client.get("/api/blend/session/me").json()["worker"] == worker

    # 책임자 로그인 → 배합 작업자 세션이 살아 있어야 한다
    _admin_login(client)
    assert client.get("/api/blend/session/me").json()["worker"] == worker

    # 책임자 로그아웃 → 여전히 유지
    client.post("/api/auth/logout", headers=_csrf(client))
    assert client.get("/api/blend/session/me").json()["worker"] == worker
    # 관리 인증은 정리됨
    assert client.get("/api/workers/all").status_code in (401, 403)


# ── 서명/시트백업 설정 — 권한 게이트 ─────────────────────────────────────────

def test_admin_config_routes_require_manager():
    client = _client()
    client.get("/api/blend/records")  # csrf 확보
    headers = _csrf(client)
    # 비로그인 차단
    assert client.put("/api/admin/signature-config", json={},
                      headers=headers).status_code in (401, 403)
    assert client.put("/api/admin/sheets-config", json={},
                      headers=headers).status_code in (401, 403)
    # 책임자는 통과 (빈 본문 = 기본값 저장)
    headers = _admin_login(client)
    assert client.put("/api/admin/signature-config", json={},
                      headers=headers).status_code == 200
