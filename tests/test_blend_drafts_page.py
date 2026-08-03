"""작성 중 배합(/blend/drafts) 페이지 라우트 + 사이드바 메뉴.

끊긴 배합/다중 계량 작업(localStorage 임시저장)을 이어서 하는 유일한 입구.
서버 상태는 없고 게이트만 있다 — 새 권한 키를 만들지 않고 기존 배합 작업자
세션 게이트(_blend_worker_or_bridge)를 그대로 쓴다(현장 무로그인 정책).
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


def _worker_session(client) -> str:
    """배합 작업자 세션 개설 — 배합 화면들과 동일한 게이트."""
    worker = "초안작업" + uuid.uuid4().hex[:6]
    client.get("/api/blend/records")  # csrf 쿠키 확보
    headers = _csrf(client)
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    headers = _csrf(client)
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/auth/logout", headers=headers)
    client.get("/api/blend/records")
    headers = _csrf(client)
    res = client.post(
        "/api/blend/session/login", json={"worker": worker}, headers=headers
    )
    assert res.status_code == 200, res.text
    return worker


def test_blend_drafts_page_requires_worker_session():
    """작업자 세션이 없으면 배합 로그인으로 보낸다(배합·다중 계량과 동일 게이트)."""
    client = _client()
    res = client.get("/blend/drafts", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/blend/login")


def test_blend_drafts_page_renders_for_worker():
    client = _client()
    _worker_session(client)

    res = client.get("/blend/drafts")
    assert res.status_code == 200, res.text
    body = res.text
    assert "작성 중 배합" in body
    # 목록이 비었을 때의 안내 + 두 화면으로 가는 버튼
    assert "이어서 할 작업이 없습니다" in body
    assert 'href="/blend"' in body
    assert 'href="/blend/continuous"' in body
    # 목록·복구는 전적으로 프론트엔드(localStorage) — 두 스크립트가 실려야 한다
    assert "/static/js/blend_drafts.js?v=20260803d" in body
    assert "/static/js/blend_drafts_page.js?v=20260803d" in body


def test_sidebar_exposes_drafts_menu_on_every_page():
    """'작성 중 배합' 메뉴는 항상 노출 — 복구 배너를 없앤 만큼 입구가 보여야 한다."""
    client = _client()
    _worker_session(client)

    for path in ("/blend", "/blend/continuous", "/blend/drafts"):
        res = client.get(path)
        assert res.status_code == 200, (path, res.status_code)
        assert 'href="/blend/drafts"' in res.text, path
        assert "작성 중 배합" in res.text, path

    # 현재 화면에서만 active 표시(다른 링크와 동일 방식)
    drafts = client.get("/blend/drafts").text
    assert 'href="/blend/drafts" class="side-link active"' in drafts
    blend = client.get("/blend").text
    assert 'href="/blend/drafts" class="side-link"' in blend


def test_blend_screens_no_longer_ship_restore_banner():
    """복구 배너 폐지 — 이어서 하기 입구는 '작성 중 배합' 한 곳뿐이다."""
    client = _client()
    _worker_session(client)

    for path in ("/blend", "/blend/continuous"):
        body = client.get(path).text
        assert "blend-restore-banner" not in body, path
        assert "cont-restore-banner" not in body, path
        assert "이어서 하시겠어요" not in body, path
    # 대신 복구 후 레시피 변경 고지 상자가 들어간다
    assert "blend-draft-notice" in client.get("/blend").text
    assert "cont-draft-notice" in client.get("/blend/continuous").text
