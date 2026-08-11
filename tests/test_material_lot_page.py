"""자재 LOT 조회 화면 페이지 라우트·사이드바 검증.

2026-08-11: 자재 LOT 은 '자재 관리'(/materials)의 한 탭으로 흡수됐고 옛 경로
(/material-lots)는 리다이렉트로 남았다. 이 파일은 옛 경로로 들어와도 화면이 뜨는지
(북마크·대시보드 링크 보호)와 사이드바가 새 경로를 가리키는지를 지킨다.
화면 자체의 탭 구성은 test_materials_page.py 가 본다.

커버:
  - GET /material-lots (리다이렉트 후) 200 + '자재 LOT' 문자열 포함
  - 비로그인에도 열린다(로그인 리다이렉트 아님)
  - 다른 페이지(/status) 응답의 사이드바가 '자재 관리' / href="/materials"
  - JS 스크립트가 ?v= 캐시버스팅과 함께 로드된다(버전 값 자체는 못박지 않는다)
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


def test_sidebar_has_materials_link_on_other_page():
    """다른 페이지(GET /status) 응답에도 사이드바 메뉴 '자재 관리' 가 보인다.

    자재 LOT 은 '자재 관리'(/materials)의 한 탭으로 흡수됐다(2026-08-11) — 사이드바
    항목명·링크가 그에 맞게 바뀌었는지 확인한다. 옛 /material-lots 는 리다이렉트로
    살아 있으므로 링크만 새 경로를 가리키면 된다.
    """
    client = _client()
    res = client.get("/status")
    assert res.status_code == 200, res.text
    assert "자재 관리" in res.text
    assert 'href="/materials"' in res.text


def test_material_lots_page_loads_script_with_version():
    """material_lots.js 가 ?v= 캐시버스팅과 함께 로드된다.

    특정 버전 값을 못박으면 버전을 올릴 때마다 테스트가 깨진다 — 검사할 것은
    '캐시버스팅이 걸려 있다'는 사실이지 오늘의 버전 문자열이 아니다.
    """
    import re

    client = _client()
    res = client.get("/material-lots")
    assert res.status_code == 200
    assert re.search(r"/static/js/material_lots\.js\?v=[0-9a-z]+", res.text)


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
