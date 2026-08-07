"""사용자 관리 통제 가드 — 2026-08-08 감사에서 재현된 3건의 회귀 방지.

① 비활성화한 담당자가 무인증 등록(배합 로그인 경로)으로 되살아나던 것
② 마지막 책임자 '해제'에만 가드가 없어 이름 기반 책임자를 0명으로 만들 수 있던 것
③ 본인 계정 비활성화가 막히지 않아 누르는 즉시 자기 잠금이 되던 것
"""

from __future__ import annotations

import importlib
import uuid


def _reload_app():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _login_admin(client):
    client.get("/api/workers")  # csrf 쿠키
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _add_worker(client, name):
    res = client.post("/api/workers", json={"name": name}, headers=_csrf(client))
    assert res.status_code == 200, res.text
    items = client.get("/api/workers/all").json()["items"]
    return next(w["id"] for w in items if w["name"] == name)


def _row(client, worker_id):
    items = client.get("/api/workers/all").json()["items"]
    return next(w for w in items if w["id"] == worker_id)


def test_inactive_worker_is_not_revived_by_unauthenticated_register():
    """배합 로그인은 이름을 넣을 때마다 POST /workers 를 무인증으로 부른다.

    종전에는 그 호출이 비활성 담당자를 그대로 되살려(reactivated=true), 책임자가
    확인창까지 눌러 내린 통제가 무효였다.
    """
    client = _reload_app()
    _login_admin(client)
    name = "가드검증" + uuid.uuid4().hex[:4]
    wid = _add_worker(client, name)

    res = client.patch(f"/api/workers/{wid}", json={"is_active": False}, headers=_csrf(client))
    assert res.status_code == 200, res.text
    assert _row(client, wid)["is_active"] is False

    # 무인증 상태로 같은 이름 등록 — 거부되고 명단은 비활성 그대로여야 한다.
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()
    client.get("/api/workers")
    denied = client.post("/api/workers", json={"name": name}, headers=_csrf(client))
    assert denied.status_code == 403, denied.text
    assert "책임자" in denied.json()["detail"]

    _login_admin(client)
    assert _row(client, wid)["is_active"] is False

    # 책임자는 되살릴 수 있다(사용자 관리의 [활성화] 경로도 동일).
    revived = client.post("/api/workers", json={"name": name}, headers=_csrf(client))
    assert revived.status_code == 200, revived.text
    assert revived.json()["reactivated"] is True
    assert _row(client, wid)["is_active"] is True


def test_last_manager_cannot_be_revoked():
    """해제에도 '마지막 책임자' 가드 — 비활성화·삭제와 같은 기준."""
    client = _reload_app()
    _login_admin(client)
    name = "가드책임자" + uuid.uuid4().hex[:4]
    wid = _add_worker(client, name)
    granted = client.post(
        f"/api/workers/{wid}/manager", json={"password": "guardpw12345"},
        headers=_csrf(client),
    )
    assert granted.status_code == 200, granted.text

    # 다른 테스트가 남긴 활성 책임자가 있으면 가드가 안 걸린다 — 이 사람만 남긴다.
    # (책임자가 2명 이상일 때의 비활성화는 허용되므로 이 정리 자체는 통과한다.)
    for other in client.get("/api/workers/all").json()["items"]:
        if other["id"] != wid and other.get("is_manager") and other.get("is_active"):
            res = client.patch(f"/api/workers/{other['id']}", json={"is_active": False},
                               headers=_csrf(client))
            assert res.status_code == 200, res.text

    # 이름 기반 책임자가 이 사람 하나뿐 → 해제 거부.
    denied = client.delete(f"/api/workers/{wid}/manager", headers=_csrf(client))
    assert denied.status_code == 400, denied.text
    assert "마지막 책임자" in denied.json()["detail"]
    assert _row(client, wid)["is_manager"] is True

    # 다른 책임자를 세우면 해제할 수 있다.
    other = "가드책임자2" + uuid.uuid4().hex[:4]
    oid = _add_worker(client, other)
    client.post(f"/api/workers/{oid}/manager", json={"password": "guardpw54321"},
                headers=_csrf(client))
    ok = client.delete(f"/api/workers/{wid}/manager", headers=_csrf(client))
    assert ok.status_code == 200, ok.text
    assert _row(client, wid)["is_manager"] is False


def test_manager_cannot_deactivate_own_account():
    """본인 비활성화 차단 — 누르는 즉시 자기 세션이 죽어 스스로 복구할 수 없었다."""
    client = _reload_app()
    _login_admin(client)
    me = "본인가드" + uuid.uuid4().hex[:4]
    other = "본인가드동료" + uuid.uuid4().hex[:4]
    my_id = _add_worker(client, me)
    other_id = _add_worker(client, other)
    for wid, pw in ((my_id, "selfguard12345"), (other_id, "selfguard54321")):
        res = client.post(f"/api/workers/{wid}/manager", json={"password": pw},
                          headers=_csrf(client))
        assert res.status_code == 200, res.text

    # 이름 기반 책임자로 다시 로그인(레거시 admin 이 아니라 본인).
    client.post("/api/auth/logout", headers=_csrf(client))
    client.cookies.clear()
    client.get("/api/workers")
    res = client.post("/api/auth/management-login",
                      json={"username": me, "password": "selfguard12345"},
                      headers=_csrf(client))
    assert res.status_code == 200, res.text

    denied = client.patch(f"/api/workers/{my_id}", json={"is_active": False},
                          headers=_csrf(client))
    assert denied.status_code == 400, denied.text
    assert "본인 계정" in denied.json()["detail"]
    assert _row(client, my_id)["is_active"] is True

    # 다른 책임자는 비활성화할 수 있다(가드는 본인에게만).
    ok = client.patch(f"/api/workers/{other_id}", json={"is_active": False},
                      headers=_csrf(client))
    assert ok.status_code == 200, ok.text
