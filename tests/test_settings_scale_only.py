"""저울 전용 입력 모드(scale-only-input) 백엔드 — 설정 on/off 엔드포인트.

스펙: scratchpad/scale-only-mode-spec.md 의 '골 1(백엔드)' 섹션.
기본 OFF — 켜기 전까지 현재 동작과 100% 동일해야 한다.

검증 항목(spec '골 1 테스트' 5케이스):
  1. GET 기본 false (행 부재 = 기본값).
  2. PUT 비책임자(미인증) → 401/403.
  3. PUT true → GET true + audit_logs 행(action=setting_scale_only_set).
  4. PUT false 왕복(true → false 로 되돌림).
  5. 잘못된 body → 400/422.

추가(서비스 헬퍼 단위):
  - get_scale_only_input: 행 없음 → False, "1" → True, "0"/기타 → False.
  - 구버전(app_settings 테이블 없음) DB → OperationalError 방어, 기본값 폴백.
"""

import importlib
import sqlite3

from src.services import settings_service


# ── TestClient(라우트) 헬퍼 ────────────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client, username="admin", password="admin"):
    """책임자 로그인 → CSRF 헤더 반환(recipe-tolerance 패턴 그대로)."""
    res = client.post(
        "/api/auth/management-login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


# ── in-memory 서비스 헬퍴 ──────────────────────────────────────
def _make_db(with_table: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if with_table:
        conn.execute(
            """
            CREATE TABLE app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            )
            """
        )
    return conn


# ============================================================
# 1. GET 기본 false (행 부재 = 기본값)
# ============================================================
def test_get_default_is_false():
    """GET /api/settings/scale-only-input 기본값 — 행이 없으면 false."""
    client = _client()
    res = client.get("/api/settings/scale-only-input")
    assert res.status_code == 200, res.text
    assert res.json() == {"enabled": False}


# ============================================================
# 2. PUT 비책임자(미인증) → 401/403
# ============================================================
def test_put_requires_manager():
    """헤더 없이(미인증) PUT → manager 권한 게이트에서 401/403."""
    client = _client()
    res = client.put("/api/settings/scale-only-input", json={"enabled": True})
    assert res.status_code in (401, 403), res.text


# ============================================================
# 3. PUT true → GET true + audit 행
# ============================================================
def test_put_true_persists_and_audits():
    """책임자가 PUT {enabled: true} → GET true, audit_logs 에 행 추가."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    # 감사 행 카운트 베이스라인
    with get_connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'setting_scale_only_set'"
        ).fetchone()[0]

    res = client.put(
        "/api/settings/scale-only-input", json={"enabled": True}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "enabled": True}

    # GET 이 true 를 반환
    got = client.get("/api/settings/scale-only-input").json()
    assert got == {"enabled": True}

    # audit_logs 에 행이 추가됐다 (details_json 에 enabled:true)
    with get_connection() as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'setting_scale_only_set'"
        ).fetchone()[0]
    assert after == before + 1

    # 설정값이 "1" 으로 저장됐다
    with get_connection() as conn:
        val = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (settings_service.SCALE_ONLY_INPUT_KEY,),
        ).fetchone()[0]
    assert val == "1"


# ============================================================
# 4. PUT false 왕복(true → false 로 되돌림)
# ============================================================
def test_put_false_roundtrip():
    """true 로 켠 뒤 false 로 끄면 GET 이 false 를 반환(왕복)."""
    client = _client()
    headers = _login(client)

    # 먼저 true 로 켬
    on = client.put(
        "/api/settings/scale-only-input", json={"enabled": True}, headers=headers
    )
    assert on.status_code == 200, on.text
    assert client.get("/api/settings/scale-only-input").json() == {"enabled": True}

    # false 로 끔
    off = client.put(
        "/api/settings/scale-only-input", json={"enabled": False}, headers=headers
    )
    assert off.status_code == 200, off.text
    assert off.json() == {"status": "ok", "enabled": False}

    # GET 이 false
    assert client.get("/api/settings/scale-only-input").json() == {"enabled": False}

    # 값이 "0" 으로 업데이트됐다(upsert — 같은 key 행 1개)
    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (settings_service.SCALE_ONLY_INPUT_KEY,),
        ).fetchone()
    assert row is not None
    assert row[0] == "0"


# ============================================================
# 5. 잘못된 body → 400/422
# ============================================================
def test_put_rejects_invalid_body():
    """enabled 가 bool 이 아니면 400/422."""
    client = _client()
    headers = _login(client)

    # 문자열 "true" (JSON bool 아님)
    res_str = client.put(
        "/api/settings/scale-only-input",
        json={"enabled": "true"},
        headers=headers,
    )
    assert res_str.status_code in (400, 422), res_str.text

    # 정수 1 (JSON bool 아님)
    res_int = client.put(
        "/api/settings/scale-only-input",
        json={"enabled": 1},
        headers=headers,
    )
    assert res_int.status_code in (400, 422), res_int.text

    # 키 자체가 없음
    res_missing = client.put(
        "/api/settings/scale-only-input", json={}, headers=headers
    )
    assert res_missing.status_code in (400, 422), res_missing.text


# ============================================================
# 부가: 서비스 헬퍼 단위 검증(방어적 폴백)
# ============================================================
def test_service_get_scale_only_input_default_when_missing():
    """행이 없으면 get_scale_only_input 은 False."""
    conn = _make_db(with_table=True)
    assert settings_service.get_scale_only_input(conn) is False


def test_service_get_scale_only_input_reflects_value():
    """value '1' → True, '0' → False, 기타 → False."""
    conn = _make_db(with_table=True)
    key = settings_service.SCALE_ONLY_INPUT_KEY
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, '1', 't', NULL)",
        (key,),
    )
    assert settings_service.get_scale_only_input(conn) is True

    conn.execute("UPDATE app_settings SET value = '0' WHERE key = ?", (key,))
    assert settings_service.get_scale_only_input(conn) is False

    conn.execute("UPDATE app_settings SET value = 'yes' WHERE key = ?", (key,))
    assert settings_service.get_scale_only_input(conn) is False


def test_service_falls_back_when_table_missing():
    """app_settings 테이블이 없는 구버전 DB → OperationalError 방어, 기본값 False."""
    conn = _make_db(with_table=False)
    # 테이블 자체가 없어도 예외 없이 False 폴백
    assert settings_service.get_scale_only_input(conn) is False
    assert settings_service.get_setting(conn, "any", default="d") == "d"
    # set 도 조용히 no-op (예외 없음)
    settings_service.set_scale_only_input(conn, True, updated_by="x")
    assert settings_service.get_scale_only_input(conn) is False


# ============================================================
# 배합 창 예외 코드(blend-window-override) — get/put(책임자) + verify(무로그인)
# ============================================================
def _csrf_only(client):
    """로그인 없이 CSRF 토큰만 확보(공개 GET 이 csrftoken 쿠키를 심는다)."""
    client.get("/api/settings/scale-only-input")
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def test_blend_window_override_default_and_verify():
    """기본 코드 111111 — GET(책임자)은 기본값, verify(무로그인+csrf)는 일치만 ok."""
    client = _client()
    headers = _login(client)

    # 다른 테스트가 코드를 바꿔뒀을 수 있으므로(공유 DB) 행을 지워 순수 기본값을 확인.
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "DELETE FROM app_settings WHERE key = ?",
            (settings_service.BLEND_WINDOW_OVERRIDE_KEY,),
        )
        conn.commit()

    got = client.get("/api/settings/blend-window-override", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json() == {"code": "111111"}

    # verify 는 무로그인(별도 클라이언트) — 로그인 없이도 동작, CSRF 만 필요.
    anon = _client()
    ch = _csrf_only(anon)
    ok = anon.post(
        "/api/settings/blend-window-override/verify", json={"code": "111111"}, headers=ch
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True}, ok.text
    bad = anon.post(
        "/api/settings/blend-window-override/verify", json={"code": "000000"}, headers=ch
    )
    assert bad.status_code == 200 and bad.json() == {"ok": False}


def test_blend_window_override_get_requires_manager():
    """GET/PUT 은 책임자 전용 — 미인증 401/403."""
    client = _client()
    assert client.get("/api/settings/blend-window-override").status_code in (401, 403)
    assert client.put(
        "/api/settings/blend-window-override", json={"code": "222222"}
    ).status_code in (401, 403)


def test_blend_window_override_change_persists_and_audits():
    """책임자가 코드 변경 → verify 가 새 코드로 통과, 옛 코드는 실패, audit 남고 값 노출 안 함."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    with get_connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'setting_blend_window_override_set'"
        ).fetchone()[0]

    res = client.put(
        "/api/settings/blend-window-override", json={"code": "77-4213"}, headers=headers
    )
    assert res.status_code == 200, res.text

    # 새 코드로 verify 통과, 기본/옛 코드는 실패(무로그인 클라이언트 + csrf)
    anon = _client()
    ch = _csrf_only(anon)
    assert anon.post(
        "/api/settings/blend-window-override/verify", json={"code": "77-4213"}, headers=ch
    ).json() == {"ok": True}
    assert anon.post(
        "/api/settings/blend-window-override/verify", json={"code": "111111"}, headers=ch
    ).json() == {"ok": False}

    # GET 이 새 코드 반환
    assert client.get(
        "/api/settings/blend-window-override", headers=headers
    ).json() == {"code": "77-4213"}

    # audit 행이 추가되고, details 에 코드 값 자체는 없다(length 만)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT details_json FROM audit_logs WHERE action = 'setting_blend_window_override_set' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        after = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action = 'setting_blend_window_override_set'"
        ).fetchone()[0]
    assert after == before + 1
    assert "77-4213" not in (row[0] or "")


def test_blend_window_override_rejects_short_code():
    """4자 미만/32자 초과 코드 → 400."""
    client = _client()
    headers = _login(client)
    assert client.put(
        "/api/settings/blend-window-override", json={"code": "12"}, headers=headers
    ).status_code == 400
    assert client.put(
        "/api/settings/blend-window-override", json={"code": "x" * 40}, headers=headers
    ).status_code == 400


def test_service_blend_window_override_helpers():
    """서비스 헬퍼 단위 — 기본값·저장·검증."""
    conn = _make_db(with_table=True)
    assert settings_service.get_blend_window_override_code(conn) == "111111"
    assert settings_service.verify_blend_window_override_code(conn, "111111") is True
    settings_service.set_blend_window_override_code(conn, "abcd12", updated_by="x")
    assert settings_service.get_blend_window_override_code(conn) == "abcd12"
    assert settings_service.verify_blend_window_override_code(conn, "abcd12") is True
    assert settings_service.verify_blend_window_override_code(conn, "111111") is False
