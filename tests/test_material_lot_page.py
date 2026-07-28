"""자재 LOT 조회·관리 화면(/material-lots) 페이지 라우트·사이드바 검증.

커버:
  - GET /material-lots 200 + '자재 LOT' 문자열 포함
  - 비로그인에도 페이지가 열린다(200, 로그인 리다이렉트 아님)
  - 다른 페이지(/status) 응답에도 사이드바 메뉴 '자재 LOT' 가 보인다
  - 사이드바 링크가 /material-lots 를 가리킨다
  - JS 스크립트가 ?v=20260728a 로 로드된다
"""

import importlib


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_material_lots_page_returns_200_and_has_heading():
    """GET /material-lots 200 + 본문에 '자재 LOT' 문자열 포함."""
    client = _client()
    res = client.get("/material-lots")
    assert res.status_code == 200, res.text
    assert "자재 LOT" in res.text


def test_material_lots_page_open_without_login():
    """비로그인에도 페이지가 열린다(리다이렉트 아닌 200)."""
    client = _client()
    # 먼저 로그아웃 상태 보장: 쿠키 없이 GET.
    res = client.get("/material-lots")
    assert res.status_code == 200
    # 리다이렉트가 아니라 본문이 렌더됐는지 — 사이드바/본문 표식 확인.
    assert "자재 LOT" in res.text


def test_sidebar_has_material_lots_link_on_other_page():
    """다른 페이지(GET /status) 응답에도 사이드바 메뉴 '자재 LOT' 가 보인다."""
    client = _client()
    res = client.get("/status")
    assert res.status_code == 200, res.text
    assert "자재 LOT" in res.text
    # 사이드바 링크가 /material-lots 를 가리킨다.
    assert 'href="/material-lots"' in res.text


def test_material_lots_page_loads_script_with_version():
    """material_lots.js 가 ?v=20260728a 로 로드된다."""
    client = _client()
    res = client.get("/material-lots")
    assert res.status_code == 200
    assert "/static/js/material_lots.js?v=20260728a" in res.text


def test_material_lots_page_shows_add_form_only_for_manager():
    """책임자 로그인 시에만 수동 LOT 추가 폼이 보인다."""
    client = _client()
    # 비로그인 — 추가 폼 없음
    anon = client.get("/material-lots")
    assert "수동 LOT 추가" not in anon.text

    # 책임자 로그인 — 추가 폼 있음
    client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    mgr = client.get("/material-lots")
    assert "수동 LOT 추가" in mgr.text
