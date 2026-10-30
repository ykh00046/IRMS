"""Recipe-Intake 원재료·코드 감시 입구와 책임자 배너 조회(2026-10-30)."""

import importlib
import uuid

from fastapi.testclient import TestClient


def _app(monkeypatch):
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    monkeypatch.setattr(cfg, "RECIPE_INTAKE_TOKEN", "")
    importlib.reload(mainmod)
    return mainmod.app


def _lan(app):
    return TestClient(app, client=("192.168.11.50", 50000))


def _items():
    return [
        {"kind": "ERP 생김", "code": "AC0092", "name": "Timiron Super Sheen MP-1001",
         "detail": "레시피에 코드 반영 필요", "action_needed": True},
        {"kind": "ERP 대기", "code": "", "name": "신규 안료", "detail": "ERP 등록 전", "action_needed": False},
    ]


def test_intake_replaces_latest_and_manager_reads_it(monkeypatch):
    app = _app(monkeypatch)
    lan = _lan(app)
    src = "t-watch-" + uuid.uuid4().hex[:6]
    res = lan.post("/api/intake/recipe-watch", json={
        "source": src, "generated_at": "2026-10-30T12:00:00", "erp_file": "ERP_2026-10-30.xlsx",
        "items": _items(),
    })
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "action_needed": 1, "items": 2}

    # 0건이면 '확인할 것 없음' — 통째로 바뀐다.
    res = lan.post("/api/intake/recipe-watch", json={"source": src, "items": []})
    assert res.json()["action_needed"] == 0

    from src.services import recipe_usage_service
    from src.db import get_connection

    with get_connection() as conn:
        latest = recipe_usage_service.latest_watch(conn, source=src)
    assert latest["received"] and latest["items"] == [] and latest["stale"] is False


def test_watch_read_requires_manager_and_shows_items(monkeypatch):
    app = _app(monkeypatch)
    lan = _lan(app)
    lan.post("/api/intake/recipe-watch", json={"erp_file": "ERP_2026-10-30.xlsx", "items": _items()})
    assert lan.get("/api/recipe-watch").status_code in (401, 403)
    assert lan.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    data = lan.get("/api/recipe-watch").json()
    assert data["received"] and data["action_needed"] == 1
    assert data["items"][0]["code"] == "AC0092"


def test_watch_intake_guards(monkeypatch):
    app = _app(monkeypatch)
    outside = TestClient(app, client=("8.8.8.8", 50000))
    assert outside.post("/api/intake/recipe-watch", json={"items": []}).status_code == 403
    tunnel = _lan(app)
    res = tunnel.post("/api/intake/recipe-watch", json={"items": []}, headers={"cf-ray": "x"})
    assert res.status_code == 403
    bad = _lan(app).post("/api/intake/recipe-watch", json={"items": [{"kind": ""}]})
    assert bad.status_code == 422


def test_stale_when_not_updated_for_a_day(monkeypatch):
    from src.db import get_connection
    from src.services import recipe_usage_service

    _app(monkeypatch)
    src = "t-stale-" + uuid.uuid4().hex[:6]
    with get_connection() as conn:
        recipe_usage_service.replace_watch(
            conn, source=src, generated_at=None, erp_file=None, items=_items(), client_ip=None
        )
        conn.execute(
            "UPDATE recipe_watch_latest SET received_at = '2026-01-01T00:00:00Z' WHERE source = ?",
            (src,),
        )
        conn.commit()
        assert recipe_usage_service.latest_watch(conn, source=src)["stale"] is True
