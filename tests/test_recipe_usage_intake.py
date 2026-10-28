"""양산대응 배합 사용량 입구(POST /api/intake/recipe-usage)·조회(GET /api/public/recipe-usage).

입구 보호: 터널(CF 헤더) 403 → 외부 IP 403 → (토큰을 설정했으면) 토큰 불일치 403.
토큰 미설정이면 사내망 요청만으로 받는다(사용자 결정 2026-10-28). 스냅샷은 (source, 기간)을 통째로 바꿔 넣으므로 같은 걸 두 번 보내도 결과가 같다.
배합 기록·반제품제조 불출량(material-usage)에는 아무것도 섞이지 않아야 한다.
"""

import importlib
import uuid

import pytest

TOKEN = "t" * 40
LAN = ("192.168.11.108", 50000)
VIA_TUNNEL = {"CF-Ray": "8a1b2c3d4e5f6789-ICN", "CF-Connecting-IP": "203.0.113.7"}


def _app(monkeypatch, token=TOKEN):
    monkeypatch.setenv("IRMS_RECIPE_INTAKE_TOKEN", token)
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod.app


def _client(app, ip=LAN):
    from fastapi.testclient import TestClient

    return TestClient(app, client=ip)


def _snapshot(source, rows=None, first_day="2026-09-29", through="2026-09-30"):
    return {
        "source": source,
        "first_day": first_day,
        "through": through,
        "computed_at": "2026-10-01T06:00:00",
        "rows": rows if rows is not None else [
            {"day": "2026-09-29", "material": "PL-700-2", "item_code": "BC1027",
             "used_g": 1000.5, "batch_g": 500.25, "lines": 3, "images": 2},
            {"day": "2026-09-29", "material": "GLYCEROL", "item_code": "AC0009",
             "used_g": 40.0, "batch_g": 40.0, "lines": 2, "images": 2},
            {"day": "2026-09-30", "material": "BLACK", "item_code": "AC0018",
             "used_g": 3.0, "lines": 1, "images": 1},
            {"day": "2026-09-30", "material": "BLACK FW-200", "item_code": "ac0018",
             "used_g": 2.0, "lines": 1, "images": 1},
            {"day": "2026-09-30", "material": "NPB", "item_code": None,
             "used_g": 1.5, "lines": 1, "images": 1},
        ],
    }


def _source():
    return "t-" + uuid.uuid4().hex[:10]


def _post(client, body, token=TOKEN, headers=None):
    return client.post("/api/intake/recipe-usage", json=body,
                       headers={"X-IRMS-Intake-Token": token, **(headers or {})})


def test_intake_guards(monkeypatch):
    app = _app(monkeypatch)
    lan = _client(app)
    body = _snapshot(_source())
    assert _post(lan, body, headers=VIA_TUNNEL).status_code == 403        # 터널은 토큰 있어도 막힘
    assert _post(_client(app, ("203.0.113.9", 4000)), body).status_code == 403  # 외부 IP
    assert _post(lan, body, token="wrong").status_code == 403
    r = lan.post("/api/intake/recipe-usage", json=body)                     # 헤더 없음
    assert r.status_code == 403 and r.json()["detail"] == "INTAKE_TOKEN_REQUIRED"
    assert _post(lan, body).status_code == 200


def test_intake_without_configured_token_is_lan_only(monkeypatch):
    """토큰 미설정(사용자 결정 2026-10-28) — 사내망이면 받고, 외부 IP·터널은 여전히 막는다."""
    app = _app(monkeypatch, token="")
    body = _snapshot(_source())
    lan = _client(app)
    assert lan.post("/api/intake/recipe-usage", json=body).status_code == 200
    assert _client(app, ("203.0.113.9", 4000)).post("/api/intake/recipe-usage", json=body).status_code == 403
    assert lan.post("/api/intake/recipe-usage", json=body, headers=VIA_TUNNEL).status_code == 403


def test_snapshot_is_idempotent_and_replaces_range(monkeypatch):
    app = _app(monkeypatch)
    lan = _client(app)
    src = _source()
    first = _post(lan, _snapshot(src)).json()
    again = _post(lan, _snapshot(src)).json()
    assert first["row_count"] == again["row_count"] == 5
    assert again["replaced"] == 5 and again["total_g"] == first["total_g"] == 1047.0
    assert again["import_id"] > first["import_id"]

    q = {"start_date": "2026-09-01", "end_date": "2026-09-30", "source": src}
    total = lan.get("/api/public/recipe-usage", params=q).json()
    assert total["total_g"] == 1047.0 and len(total["items"]) == 5

    # 다음 날 장부: 9/30 값이 바뀌고(답글로 정정) 10/1 이 붙는다. 범위 밖 9/29 는 보내지 않아도 남는다.
    _post(lan, _snapshot(src, first_day="2026-09-30", through="2026-10-01", rows=[
        {"day": "2026-09-30", "material": "BLACK", "item_code": "AC0018", "used_g": 6.0},
        {"day": "2026-10-01", "material": "PL-700-2", "item_code": "BC1027", "used_g": 200.0},
    ]))
    by_day = lan.get("/api/public/recipe-usage", params={**q, "end_date": "2026-10-31", "group": "day"}).json()
    got = {(i["period"], i["material_name"]): i["used_g"] for i in by_day["items"]}
    assert got == {("2026-09-29", "PL-700-2"): 1000.5, ("2026-09-29", "GLYCEROL"): 40.0,
                   ("2026-09-30", "BLACK"): 6.0, ("2026-10-01", "PL-700-2"): 200.0}


def test_kind_filter_month_group_and_status(monkeypatch):
    app = _app(monkeypatch)
    lan = _client(app)
    src = _source()
    from src.db import get_connection

    # 공용 테스트 DB 의 마스터를 비어 있지 않게 남기면 뒤 테스트(미지 자재 차단)가 깨진다 — 넣은 줄은 지운다.
    conn = get_connection()
    with conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO item_code_master (code, name, kind, imported_at) "
            "VALUES ('AC0009', 'GLYCEROL', 'material', '2026-10-28T00:00:00Z')"
        ).rowcount
    try:
        res = _post(lan, _snapshot(src)).json()
    finally:
        if inserted:
            with conn:
                conn.execute("DELETE FROM item_code_master WHERE code = 'AC0009'")
        conn.close()
    # 마스터에 없는 코드는 저장하되 표면화(예: 아직 미입고 품목). AC0009 는 마스터에 있다.
    assert "AC0009" not in res["unknown_codes"]
    assert set(res["unknown_codes"]) <= {"AC0018", "BC1027"}

    q = {"start_date": "2026-09-01", "end_date": "2026-09-30", "source": src}
    a = lan.get("/api/public/recipe-usage", params={**q, "kind": "A"}).json()
    assert {i["item_code"] for i in a["items"]} == {"AC0009", "AC0018"}   # 소문자 코드도 대문자로 정리
    assert a["total_g"] == 45.0
    b = lan.get("/api/public/recipe-usage", params={**q, "kind": "B"}).json()
    assert [i["material_name"] for i in b["items"]] == ["PL-700-2"]
    m = lan.get("/api/public/recipe-usage", params={**q, "group": "month"}).json()
    assert {i["period"] for i in m["items"]} == {"2026-09"} and m["unit"] == "g"

    st = lan.get("/api/public/recipe-usage/status", params={"source": src}).json()
    assert st["received"] and st["through"] == "2026-09-30" and st["row_count"] == 5
    empty = lan.get("/api/public/recipe-usage/status", params={"source": "t-none-" + uuid.uuid4().hex[:6]})
    assert empty.json()["received"] is False


@pytest.mark.parametrize("bad", [
    {"rows": [{"day": "2026-10-05", "material": "X", "used_g": 1}]},              # 기간 밖
    {"rows": [{"day": "2026-09-29", "material": "X", "used_g": -1}]},             # 음수
    {"rows": [{"day": "2026-09-29", "material": "X", "used_g": 1},
              {"day": "2026-09-29", "material": "X", "used_g": 2}]},              # 중복
    {"rows": [{"day": "2026-09-29", "material": "X", "item_code": "BC10;--", "used_g": 1}]},
    {"first_day": "2026-10-01"},                                                  # first_day > through
    {"through": "2026-02-30"},
])
def test_intake_rejects_bad_payload(monkeypatch, bad):
    app = _app(monkeypatch)
    body = {**_snapshot(_source()), **bad}
    assert _post(_client(app), body).status_code == 422


def test_public_get_keeps_lan_protection(monkeypatch):
    app = _app(monkeypatch)
    outside = _client(app, ("203.0.113.9", 4000))
    assert outside.get("/api/public/recipe-usage").status_code == 403


def test_material_usage_untouched(monkeypatch):
    """입구로 넣은 사용량이 반제품제조 불출량(material-usage)에 섞이면 대시보드가 이중 차감한다."""
    app = _app(monkeypatch)
    lan = _client(app)
    q = {"start_date": "2026-09-29", "end_date": "2026-09-30"}
    before = lan.get("/api/public/material-usage", params=q).json()
    _post(lan, _snapshot(_source()))
    after = lan.get("/api/public/material-usage", params=q).json()
    assert after == before
