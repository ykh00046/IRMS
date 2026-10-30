"""전체 검토(2026-10-30) 낮음 등급 5건의 회귀 테스트.

④ 같은 멱등 키로 값을 고쳐 다시 보내면 첫 기록을 '저장 완료'로 돌려주지 않고 409
⑤ 터널 경유 요청은 Cloudflare 가 넘긴 실제 IP 로 rate-limit 버킷을 나눈다(루프백일 때만)
⑥ 완전 삭제된 제품 LOT 은 다시 발급하지 않는다
⑦ 멱등 재응답(기록 내용)은 작업자 세션이 있어야 받는다
⑧ 작업자 세션 활동 시각은 5분 안에는 다시 쓰지 않는다(쿠키 덮어쓰기 경합 축소)
"""

import datetime as dt
import uuid

from tests.test_blend_save_integrity import _import_recipe, _mgmt_client, _uid, _worker_session


def _single_body(rid, product, worker, a=60, b=40, lot_a="LA"):
    return {
        "recipe_id": rid, "product_name": product, "worker": worker,
        "work_date": "2026-10-30", "total_amount": 100,
        "request_id": "req-" + uuid.uuid4().hex,
        "details": [
            {"material_name": "원료A", "actual_amount": a, "material_lot": lot_a},
            {"material_name": "원료B", "actual_amount": b, "material_lot": "LB"},
        ],
    }


def test_changed_resend_with_same_request_id_is_rejected():
    client, csrf = _mgmt_client()
    product = "재전송" + _uid()
    worker = _worker_session(client, csrf, "재전송작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    body = _single_body(rid, product, worker)
    first = client.post("/api/blend/records", json=body, headers=csrf())
    assert first.status_code == 200, first.text

    # 같은 내용 재전송(응답 유실 재시도) — 첫 기록 그대로.
    again = client.post("/api/blend/records", json=body, headers=csrf())
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]

    # LOT 오타를 고쳐 같은 키로 보냄 — 고친 값이 사라진 채 '완료'가 되면 안 된다.
    fixed = {**body, "details": [{**body["details"][0], "material_lot": "LA-FIX"}, body["details"][1]]}
    res = client.post("/api/blend/records", json=fixed, headers=csrf())
    assert res.status_code == 409
    assert first.json()["product_lot"] in res.json()["detail"]
    assert "정정" in res.json()["detail"]


def test_continuous_changed_resend_is_rejected():
    client, csrf = _mgmt_client()
    product = "재전송연속" + _uid()
    _worker_session(client, csrf, "재전송연속작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    lot = [
        {"material_name": "원료A", "actual_amount": 60, "material_lot": "LA"},
        {"material_name": "원료B", "actual_amount": 40, "material_lot": "LB"},
    ]
    body = {
        "recipe_id": rid, "product_name": product, "work_date": "2026-10-30",
        "total_amount": 100, "request_id": "req-" + uuid.uuid4().hex, "lots": [lot, lot],
    }
    assert client.post("/api/blend/records/continuous", json=body, headers=csrf()).status_code == 200
    changed = {**body, "lots": [lot, [lot[0], {**lot[1], "material_lot": "LB-FIX"}]]}
    assert client.post("/api/blend/records/continuous", json=changed, headers=csrf()).status_code == 409


def test_resend_requires_worker_session():
    client, csrf = _mgmt_client()
    product = "재응답" + _uid()
    worker = _worker_session(client, csrf, "재응답작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    body = _single_body(rid, product, worker)
    assert client.post("/api/blend/records", json=body, headers=csrf()).status_code == 200
    assert client.post("/api/blend/session/logout", headers=csrf()).status_code == 200
    res = client.post("/api/blend/records", json=body, headers=csrf())
    assert res.status_code == 401


def test_hard_deleted_lot_is_not_reissued():
    client, csrf = _mgmt_client()
    product = "번호" + _uid()
    worker = _worker_session(client, csrf, "번호작업" + _uid())
    rid = _import_recipe(client, csrf, product, [("원료A", 60), ("원료B", 40)])
    first = client.post("/api/blend/records", json=_single_body(rid, product, worker), headers=csrf()).json()
    second = client.post("/api/blend/records", json=_single_body(rid, product, worker), headers=csrf()).json()
    assert second["product_lot"].endswith("02")
    res = client.delete(
        f"/api/blend/records/{second['id']}", params={"hard": True, "reason": "잘못 저장"},
        headers=csrf(),
    )
    assert res.status_code == 200, res.text
    third = client.post("/api/blend/records", json=_single_body(rid, product, worker), headers=csrf()).json()
    assert third["product_lot"].endswith("03"), third["product_lot"]
    assert first["product_lot"].endswith("01")


class _Req:
    def __init__(self, host, headers):
        self.client = type("C", (), {"host": host})()
        self.headers = headers
        self.scope = {"type": "http"}


def test_rate_limit_key_uses_cloudflare_ip_only_via_loopback():
    from src.limiter import client_key

    tunnel = _Req("127.0.0.1", {"cf-ray": "abc", "cf-connecting-ip": "203.0.113.7"})
    assert client_key(tunnel) == "203.0.113.7"
    # 사내망 PC 가 헤더를 꾸며도 자기 IP 로 묶인다.
    spoof = _Req("192.168.11.50", {"cf-ray": "abc", "cf-connecting-ip": "203.0.113.7"})
    assert client_key(spoof) == "192.168.11.50"
    assert client_key(_Req("127.0.0.1", {})) == "127.0.0.1"


def test_touch_skips_rewrite_within_interval(monkeypatch):
    from src import blend_session as bs

    class _Session(dict):
        writes = 0

        def __setitem__(self, k, v):
            type(self).writes += 1
            super().__setitem__(k, v)

    req = type("R", (), {})()
    req.session = _Session()
    base = dt.datetime(2026, 10, 30, 9, 0, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(bs, "_utc_now", lambda: base)
    bs.login_worker_session(req, "홍길동")
    _Session.writes = 0
    monkeypatch.setattr(bs, "_utc_now", lambda: base + dt.timedelta(minutes=2))
    bs.touch_worker_session(req)  # 2분 하트비트 — 세션을 고치지 않는다
    assert _Session.writes == 0
    monkeypatch.setattr(bs, "_utc_now", lambda: base + dt.timedelta(minutes=6))
    bs.touch_worker_session(req)
    assert _Session.writes == 1
    assert bs.current_blend_worker(req) == "홍길동"
