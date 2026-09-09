"""AI 도우미 설정·상태 API 테스트.

확인 사항: 키 가리기, 환경변수 폴백, 빈 문자열로 삭제, 책임자 전용, 감사 로그.
"""

import importlib

import pytest


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _login_admin(client):
    client.get("/api/blend/records")  # CSRF 쿠키 확보
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _clear_keys():
    from src.db import get_connection
    from src.services.assistant import settings as aset

    with get_connection() as conn:
        for key in (aset.GEMINI_KEY, aset.GROQ_KEY, aset.ENABLED_KEY, aset.MODEL_KEY):
            conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        conn.commit()


@pytest.fixture(autouse=True)
def _reset_assistant_settings():
    yield
    try:
        _clear_keys()
    except Exception:  # noqa: BLE001 - 정리 실패가 다음 테스트를 막지 않게
        pass


def test_settings_requires_manager():
    """무로그인은 설정을 읽지도 쓰지도 못한다."""
    client = _client()
    client.get("/api/blend/records")
    assert client.get("/api/assistant/settings").status_code in (401, 403)
    res = client.put(
        "/api/assistant/settings", json={"enabled": True}, headers=_csrf(client)
    )
    assert res.status_code in (401, 403)


def test_operator_level_is_below_manager():
    """담당자 권한으로는 책임자 게이트를 넘지 못한다."""
    from src.auth import has_access_level

    assert has_access_level({"access_level": "operator"}, "manager") is False
    assert has_access_level({"access_level": "manager"}, "manager") is True


def test_put_masks_key_and_reports_source():
    client = _client()
    _login_admin(client)
    _clear_keys()

    res = client.put(
        "/api/assistant/settings",
        json={
            "enabled": True,
            "model": "gemini-2.5-flash-lite",
            "gemini_api_key": "AIzaSyABCD1234567890WXYZ",
        },
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text

    body = client.get("/api/assistant/settings").json()
    assert body["enabled"] is True
    assert body["model"] == "gemini-2.5-flash-lite"
    assert body["gemini_set"] is True
    assert body["gemini_source"] == "db"
    assert body["gemini_key_masked"] == "AIza…WXYZ"
    # 원문 키가 어떤 값으로도 새 나가면 안 된다.
    assert "AIzaSyABCD1234567890WXYZ" not in res.text
    assert "AIzaSyABCD1234567890WXYZ" not in str(body)


def test_empty_string_deletes_key_and_env_takes_over(monkeypatch):
    client = _client()
    _login_admin(client)
    _clear_keys()

    client.put(
        "/api/assistant/settings",
        json={"gemini_api_key": "AIzaSyABCD1234567890WXYZ"},
        headers=_csrf(client),
    )
    assert client.get("/api/assistant/settings").json()["gemini_source"] == "db"

    client.put(
        "/api/assistant/settings", json={"gemini_api_key": ""}, headers=_csrf(client)
    )
    body = client.get("/api/assistant/settings").json()
    assert body["gemini_set"] is False
    assert body["gemini_source"] is None
    assert body["gemini_key_masked"] is None

    # DB 행이 없으면 환경변수가 대신 쓰인다.
    import src.config as cfg

    monkeypatch.setattr(cfg, "GEMINI_API_KEY", "ENVKEY0123456789")
    body = client.get("/api/assistant/settings").json()
    assert body["gemini_set"] is True
    assert body["gemini_source"] == "env"
    assert body["gemini_key_masked"] == "ENVK…6789"


def test_put_writes_audit_row():
    client = _client()
    _login_admin(client)
    _clear_keys()

    client.put(
        "/api/assistant/settings",
        json={"enabled": True, "groq_api_key": "gsk_test_1234567890"},
        headers=_csrf(client),
    )

    from src.db import get_connection, list_audit_logs

    with get_connection() as conn:
        rows = list_audit_logs(conn, limit=20, action="setting_assistant_set")
    assert rows, "감사 로그가 남지 않았다"
    details = rows[0]["details"]
    assert details.get("enabled") is True
    assert details.get("groq_api_key") == "set"
    # 감사 로그에도 키 값은 남지 않는다.
    assert "gsk_test_1234567890" not in str(rows[0])


def test_status_disabled_without_key():
    client = _client()
    _clear_keys()

    import src.config as cfg

    cfg.ASSISTANT_FAKE = False
    cfg.GEMINI_API_KEY = ""
    cfg.GROQ_API_KEY = ""

    body = client.get("/api/assistant/status").json()
    assert body["enabled"] is False
    assert body["provider"] is None


def test_status_enabled_with_fake_mode():
    client = _client()
    _login_admin(client)
    _clear_keys()
    client.put(
        "/api/assistant/settings", json={"enabled": True}, headers=_csrf(client)
    )

    import src.config as cfg

    cfg.ASSISTANT_FAKE = True
    try:
        body = client.get("/api/assistant/status").json()
    finally:
        cfg.ASSISTANT_FAKE = False
    assert body["enabled"] is True
    assert body["provider"] == "fake"


def test_mask_key_hides_short_values():
    from src.services.assistant.settings import mask_key

    assert mask_key(None) is None
    assert mask_key("") is None
    assert mask_key("short") == "…"
    assert mask_key("abcdefghij") == "abcd…ghij"
