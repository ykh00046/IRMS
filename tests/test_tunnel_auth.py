"""터널(외부) 경유 요청에만 책임자 로그인을 요구하는지 검사.

현장 화면 무로그인은 **사내망 한정** 정책이다. cloudflared 가 앱 전체를 인터넷에
게시하는 상태에서는 주소만 알면 전 배합 기록 엑셀(`/export-all`)·배합일지 전량
(`/dhr-zip`)을 받아갈 수 있고, 이름 등록 → 작업자 세션 → 기록 생성으로 **가짜
생산기록을 넣을 수도** 있다(2026-07-26 실측 확인). 이 테스트는 그 두 가지가 터널
경유로는 막히고, 사내망에서는 종전과 똑같이 동작하는지 고정한다.
"""

import importlib

import pytest

# Cloudflare 엣지가 붙이는 헤더 — 이게 있으면 터널 경유로 판별한다.
# 엣지가 클라이언트 발 동명 헤더를 덮어쓰므로 외부에서 지워 사내망인 척할 수 없다.
VIA_TUNNEL = {"CF-Ray": "8a1b2c3d4e5f6789-ICN", "CF-Connecting-IP": "203.0.113.7"}

# 인터넷에 열려 있으면 안 되는 것들 — 전 생산기록 반출 경로.
EXPORT_PATHS = [
    "/api/blend/records",
    "/api/blend/records/export-all",
    "/api/blend/records/dhr-zip",
    "/api/blend/material-usage",
    "/api/recipes",
]


@pytest.fixture()
def client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def test_lan_requests_are_untouched(client):
    """사내망 요청에는 아무 변화가 없어야 한다 — 현장이 멈추면 안 된다."""
    for path in EXPORT_PATHS:
        assert client.get(path).status_code != 403, f"사내망에서 {path} 가 막혔습니다"


def test_tunnel_requests_need_manager_login(client):
    for path in EXPORT_PATHS:
        response = client.get(path, headers=VIA_TUNNEL)
        assert response.status_code == 403, f"터널에서 {path} 가 그대로 열려 있습니다"


def test_tunnel_blocks_fabricated_production_records(client):
    """이름 등록 → 작업자 세션 → 기록 생성 연쇄가 외부에서 끊겨야 한다."""
    client.get("/api/blend/records")  # csrf 쿠키 확보
    head = {**VIA_TUNNEL, **_csrf(client)}
    assert client.post("/api/workers", json={"name": "외부침입자"}, headers=head).status_code == 403
    assert client.post("/api/blend/session/login", json={"worker": "외부침입자"},
                       headers=head).status_code == 403
    assert client.post("/api/blend/records", json={}, headers=head).status_code == 403


def test_tunnel_allows_login_and_its_assets(client):
    """로그인 자체는 통과해야 한다 — 아니면 외부에서 영원히 못 들어온다."""
    assert client.get("/management/login", headers=VIA_TUNNEL).status_code == 200
    assert client.get("/health", headers=VIA_TUNNEL).status_code == 200
    assert client.get("/static/css/common.css", headers=VIA_TUNNEL).status_code == 200
    # 자격증명이 틀려도 문지기(403)가 아니라 인증 로직까지 도달해야 한다.
    bad = client.post(
        "/api/auth/management-login",
        json={"username": "admin", "password": "definitely-wrong"},
        headers=VIA_TUNNEL,
    )
    assert bad.status_code != 403


def test_tunnel_html_redirects_to_login_with_next(client):
    response = client.get("/status", headers=VIA_TUNNEL, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/management/login?next=/status"


def test_logged_in_manager_passes_through_the_tunnel(client):
    """외부에서도 책임자로 로그인하면 평소처럼 쓸 수 있어야 한다."""
    login = client.post(
        "/api/auth/management-login",
        json={"username": "admin", "password": "admin"},
        headers=VIA_TUNNEL,
    )
    assert login.status_code == 200, login.text
    assert client.get("/api/blend/records", headers=VIA_TUNNEL).status_code == 200


def test_spoofed_header_from_lan_only_tightens(client):
    """사내망에서 헤더를 위조해도 더 엄격해질 뿐 — 우회로가 되지 않는다."""
    assert client.get("/api/blend/records/export-all",
                      headers={"CF-Ray": "forged"}).status_code == 403


def test_login_page_hides_manager_roster_from_the_internet(client):
    """자동완성 목록은 유효한 계정명 그 자체다 — 외부에는 내주지 않는다."""
    external = client.get("/management/login", headers=VIA_TUNNEL).text
    assert "<datalist" in external          # 화면 구조는 그대로
    assert "<option value=" not in external, "외부에 책임자 이름이 노출됩니다"

    internal = client.get("/management/login").text
    assert "<option value=" in internal, "사내망 자동완성이 사라졌습니다"
