"""/api/version 배포 마커 엔드포인트 검증.

core.js 가 이 값을 폴링해 변하면 자동 새로고침한다. 인증 불필요·GET(CSRF 면제)·
상수 반환이라 저비용. 계약: 200 + 비어있지 않은 version 문자열.
"""

import importlib


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_version_endpoint_returns_nonempty_version():
    client = _client()
    res = client.get("/api/version")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "version" in body
    assert isinstance(body["version"], str)
    assert body["version"].strip() != ""


def test_version_endpoint_is_public_and_stable():
    """로그인 없이 접근 가능하고, 재시작 전까지 값이 고정(상수)이다."""
    client = _client()
    first = client.get("/api/version")
    second = client.get("/api/version")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["version"] == second.json()["version"]
