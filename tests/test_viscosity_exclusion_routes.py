"""점도 측정 '통계 제외/해제' HTTP 라우트 검증(정책 ⓑ — 책임자 전용).

서비스 계층(exclude_reading/include_reading)은 tests/test_viscosity_exclusion.py 에서
이미 검증한다. 여기서는 mgr_router 의 HTTP 계약만 본다:
  - 비로그인(익명) → 인증 게이트에서 401
  - 사유 스키마 위반(빈 문자열/필드 누락) → 422 (Pydantic min_length=1)
  - 공백-only 사유 → 400 (라우트가 서비스 ValueError 를 400 으로 매핑)
  - 알 수 없는 reading id → 404
  - 성공 → 200, excluded=1 + 감사 로그, include 로 복원

하네스 주의(conftest.py 참조): 테스트 DB 는 실행 전체에서 공유되고 공용 admin 계정은
단일 session_token 을 쓴다. 관리 세션을 여러 요청에 걸쳐 오래 들고 있으면(특히
required=False 인 등록 요청이 중간에 끼면) 간헐 401 이 난다. 그래서
  - 스키마/공백/404 테스트는 시드 없이 로그인 + 단일 보호 요청만 한다
    (해당 오류는 실제 reading 유무와 무관하게 발생한다).
  - 왕복(성공) 테스트만 제품/측정을 DB 에 직접 심어(라우트 시드 요청 배제) 보호
    요청을 exclude/include 두 번으로 줄인다.
"""

import importlib
import uuid


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _manager_client():
    client = _client()
    login = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert login.status_code == 200, login.text
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    return client, headers


def _seed_reading_direct():
    """제품 + 측정 1건을 공유 테스트 DB 에 직접 삽입(라우트 인증/시드 요청 우회).

    (product_id, reading_id) 반환. exclude/include 대상만 있으면 되므로 최소 컬럼만 채운다.
    """
    from src.db import get_connection, utc_now_text

    code = "EXR" + uuid.uuid4().hex[:8].upper()
    now = utc_now_text()
    with get_connection() as conn:
        pid = int(
            conn.execute(
                "INSERT INTO viscosity_products (code, name, sigma_k, is_active, created_at) "
                "VALUES (?, ?, 3, 1, ?)",
                (code, code, now),
            ).lastrowid
        )
        rid = int(
            conn.execute(
                "INSERT INTO viscosity_readings "
                "(product_id, lot_no, viscosity, measured_date, created_by, created_at, excluded) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (pid, f"{code}L1", 50.0, "2026-01-05", "test", now),
            ).lastrowid
        )
        conn.commit()
    return pid, rid


# ── (a) 익명 = 인증 게이트에서 401 ─────────────────────────────────
def test_exclude_denied_for_anonymous():
    """CSRF 토큰이 있어도 비로그인이면 mgr_router 인증(require_access_level)에서 막힌다."""
    client = _client()
    client.get("/viscosity")  # csrftoken 쿠키 확보
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}

    r = client.post(
        "/api/viscosity/readings/1/exclude", json={"reason": "테스트"}, headers=headers
    )
    assert r.status_code == 401, r.text
    ri = client.post("/api/viscosity/readings/1/include", headers=headers)
    assert ri.status_code == 401, ri.text


# ── (b) 빈/누락 사유 = 스키마(422), 공백 사유 = 라우트(400) ─────────
def test_exclude_empty_reason_is_422_schema_level():
    """reason='' 은 ViscosityExcludeBody(min_length=1) 위반 → 422 (reading 유무 무관)."""
    client, headers = _manager_client()
    r = client.post(
        "/api/viscosity/readings/1/exclude", json={"reason": ""}, headers=headers
    )
    assert r.status_code == 422, r.text


def test_exclude_missing_reason_field_is_422():
    """reason 필드 누락도 스키마 위반 → 422."""
    client, headers = _manager_client()
    r = client.post("/api/viscosity/readings/1/exclude", json={}, headers=headers)
    assert r.status_code == 422, r.text


def test_exclude_whitespace_reason_is_400_route_level():
    """공백-only 사유는 스키마(min_length=1)는 통과하나 서비스가 strip 후 거부 → 400.

    서비스는 row 조회 전에 사유부터 검사하므로 존재하지 않는 id 여도 400 이 난다.
    """
    client, headers = _manager_client()
    r = client.post(
        "/api/viscosity/readings/1/exclude", json={"reason": "   "}, headers=headers
    )
    assert r.status_code == 400, r.text


# ── (c) 알 수 없는 id = 404 ────────────────────────────────────────
def test_exclude_unknown_reading_id_is_404():
    client, headers = _manager_client()
    r = client.post(
        "/api/viscosity/readings/999999/exclude", json={"reason": "사유"}, headers=headers
    )
    assert r.status_code == 404, r.text


def test_include_unknown_reading_id_is_404():
    client, headers = _manager_client()
    r = client.post("/api/viscosity/readings/999999/include", headers=headers)
    assert r.status_code == 404, r.text


# ── (d) 성공 = 200 + excluded 표시 + 감사 로그, include 로 복원 ────
def test_exclude_then_include_roundtrip_marks_and_restores():
    from src.db import get_connection

    pid, rid = _seed_reading_direct()
    client, headers = _manager_client()

    # 제외
    r = client.post(
        f"/api/viscosity/readings/{rid}/exclude",
        json={"reason": "측정 오류"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "id": rid}

    with get_connection() as conn:
        row = conn.execute(
            "SELECT excluded, exclude_reason FROM viscosity_readings WHERE id = ?", (rid,)
        ).fetchone()
        assert row["excluded"] == 1
        assert row["exclude_reason"] == "측정 오류"
        audit = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_logs "
            "WHERE action = 'viscosity_reading_excluded' AND target_id = ?",
            (str(rid),),
        ).fetchone()
        assert audit["c"] >= 1

    # 복원
    ri = client.post(f"/api/viscosity/readings/{rid}/include", headers=headers)
    assert ri.status_code == 200, ri.text
    assert ri.json() == {"ok": True, "id": rid}

    with get_connection() as conn:
        row = conn.execute(
            "SELECT excluded, exclude_reason FROM viscosity_readings WHERE id = ?", (rid,)
        ).fetchone()
        assert row["excluded"] == 0
        assert row["exclude_reason"] is None
        audit = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_logs "
            "WHERE action = 'viscosity_reading_restored' AND target_id = ?",
            (str(rid),),
        ).fetchone()
        assert audit["c"] >= 1
