"""점도 알림 정리 기준일 — 지난 배합분은 알림에서 빠진다.

현장 요청(2026-08-07): 지나간 배합은 이제 와서 점도를 잴 수 없는데도 알림 대상 품목이면
현장 도우미 팝업이 매일 떴다. 책임자가 [지금까지 정리]를 누르면 그 시점 이전 배합은
알림에서 빠지고, 이후에 배합한 것만 다시 알린다. 기록 자체는 건드리지 않는다.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import date, timedelta


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


def _seed_recipe(client, prod):
    """점도 반제품 등록은 같은 이름의 레시피를 요구한다 — TSV 임포트로 하나 만든다."""
    raw = f"반제품명\tA\n{prod}\t100"
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": raw, "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _make_product_and_blend(client, prod, work_date):
    """알림 대상(remind_daily) 점도 반제품 + 같은 이름의 배합 기록 1건."""
    _seed_recipe(client, prod)
    created = client.post(
        "/api/viscosity/products",
        json={"code": prod, "name": prod},
        headers=_csrf(client),
    )
    assert created.status_code in (200, 201), created.text
    # 매일 알림 플래그는 생성이 아니라 수정에서 켠다(라우트 규약).
    listed = client.get("/api/viscosity/products").json()["items"]
    pid = next(p["id"] for p in listed if p["code"] == prod)
    upd = client.patch(
        f"/api/viscosity/products/{pid}",
        json={"name": prod, "remind_daily": True, "is_active": True},
        headers=_csrf(client),
    )
    assert upd.status_code == 200, upd.text
    worker = "점도알림" + uuid.uuid4().hex[:4]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": worker}, headers=_csrf(client))
    rec = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": work_date,
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": "A", "ratio": 100, "theory_amount": 100,
             "actual_amount": 100, "material_lot": "L1"},
        ],
    }, headers=_csrf(client))
    assert rec.status_code == 200, rec.text


def _due_codes(client):
    """트레이가 부르는 알림 목록. 내부망 제한이 걸린 라우트라 사내 IP 로 접속한다."""
    from fastapi.testclient import TestClient

    internal = TestClient(client.app, client=("192.168.11.108", 50000))
    res = internal.get("/api/public/viscosity-reminders/due")
    assert res.status_code == 200, res.text
    return {item["code"] for item in res.json()["items"]}


def test_reminder_since_hides_past_blends_and_keeps_new_ones():
    client = _reload_app()
    client.get("/api/blend/records")  # csrf 쿠키
    client.post("/api/auth/management-login", json={"username": "admin", "password": "admin"})

    old_prod = "VOLD" + uuid.uuid4().hex[:4].upper()
    long_ago = (date.today() - timedelta(days=60)).isoformat()
    _make_product_and_blend(client, old_prod, long_ago)

    # 기준일 없음 → 종전 동작: 대상 품목이면 오늘 측정이 없으니 알림에 뜬다.
    assert old_prod in _due_codes(client)

    # [지금까지 정리] — 기준일을 오늘로.
    res = client.post("/api/settings/viscosity-reminder-since", headers=_csrf(client))
    assert res.status_code == 200, res.text
    assert res.json()["since"] == date.today().isoformat()

    # 60일 전 배합분은 이제 알림에서 빠진다.
    assert old_prod not in _due_codes(client)

    # 기준일 이후에 배합한 반제품은 다시 알린다.
    new_prod = "VNEW" + uuid.uuid4().hex[:4].upper()
    _make_product_and_blend(client, new_prod, date.today().isoformat())
    assert new_prod in _due_codes(client)


def test_reminder_since_requires_manager_and_is_readable_openly():
    client = _reload_app()
    client.get("/api/blend/records")
    # 미로그인 POST 는 거부(책임자 전용 통제).
    denied = client.post("/api/settings/viscosity-reminder-since", headers=_csrf(client))
    assert denied.status_code in (401, 403), denied.text
    # GET 은 공개 — 점도 화면이 현재 기준일을 보여준다.
    res = client.get("/api/settings/viscosity-reminder-since")
    assert res.status_code == 200, res.text
    assert "since" in res.json()
