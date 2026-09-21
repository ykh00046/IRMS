"""PB 연계 탭(2026-09-21) — 왜 안 붙었는지, 그리고 PB LOT 으로 거꾸로 찾기.

연계 화면은 "이 반제품이 쓴 PB" 한 방향만 있었고, 붙은 건수만 말해 못 붙은 이유를
구별할 수 없었다(운영 실측: APB 363건 중 264건 미연계, 그 263건이 'PB 점도 기록 없음').
여기서 세 가지를 못 박는다.

  1. analyze_product 의 pb_link 사유 건수 — matched/no_lot/lot_unreadable/pb_missing/
     pb_excluded 의 합이 이 조회 범위의 측정 건수와 같다.
  2. GET /api/viscosity/pb-lots · /pb-lots/{lot_no} — PB LOT 으로 거꾸로 찾기.
  3. GET /api/blend/product-lot-viscosity 의 managed — 배합 화면이 '일반 원료'와
     '점도 반제품인데 이 LOT 만 기록 없음'을 구별한다.
"""

from __future__ import annotations

import importlib
import uuid


# ── 공통 헬퍼 ────────────────────────────────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client) -> dict:
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _login_admin(client):
    client.get("/api/blend/records")  # csrf 쿠키
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _digits8() -> str:
    """이 테스트 안에서만 쓰는 8자리 LOT 숫자 — 다른 테스트의 PB 와 섞이지 않게."""
    return f"{uuid.uuid4().int % 10**8:08d}"


def _seed_recipe(client, product, materials=("PB", "MEK")):
    lines = ["반제품명\t" + "\t".join(materials), product + "\t" + "\t".join(["50"] * len(materials))]
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": "\n".join(lines), "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _make_product(client, code, **limits) -> int:
    _seed_recipe(client, code)
    body = {"code": code, "name": code}
    body.update(limits)
    created = client.post("/api/viscosity/products", json=body, headers=_csrf(client))
    assert created.status_code in (200, 201), created.text
    return int(created.json()["id"])


def _pb_product_id(client) -> int:
    """PB 반제품 — 이미 있으면 그것을 쓴다(전역 코드라 테스트마다 새로 못 만든다)."""
    listed = client.get("/api/viscosity/products").json()["items"]
    for item in listed:
        if item["code"] == "PB":
            return int(item["id"])
    return _make_product(client, "PB")


def _add_reading(client, product_id, lot, value, date, *, material_lot=None) -> int:
    body = {
        "product_id": product_id,
        "lot_no": lot,
        "viscosity": value,
        "measured_date": date,
    }
    if material_lot is not None:
        body["material_lot"] = material_lot
    res = client.post("/api/viscosity/readings", json=body, headers=_csrf(client))
    assert res.status_code == 200, res.text
    found = next((r for r in res.json()["readings"] if r["lot_no"] == lot), None)
    assert found, f"방금 등록한 측정이 목록에 없다: {lot}"
    return int(found["id"])


def _exclude(client, reading_id, reason="시험 재측정"):
    res = client.post(
        f"/api/viscosity/readings/{reading_id}/exclude",
        json={"reason": reason},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _analyze(client, product_id) -> dict:
    res = client.get(f"/api/viscosity/products/{product_id}")
    assert res.status_code == 200, res.text
    return res.json()


# ── 1. pb_link 사유 건수 ─────────────────────────────────────────────
def test_pb_link_counts_every_reason_and_they_sum_to_the_readings_in_view():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)

    matched_lot = _digits8()
    excluded_lot = _digits8()
    missing_lot = _digits8()          # PB 측정을 아예 만들지 않는다
    _add_reading(client, pb_id, f"PB{matched_lot}", 49.0, "2026-09-01")
    excluded_id = _add_reading(client, pb_id, f"PB{excluded_lot}", 52.0, "2026-09-02")
    _exclude(client, excluded_id)

    code = "PBL" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-03", material_lot=matched_lot)
    _add_reading(client, pid, f"{code}-2", 310.0, "2026-09-04", material_lot=excluded_lot)
    _add_reading(client, pid, f"{code}-3", 320.0, "2026-09-05", material_lot=missing_lot)
    # 숫자가 하나도 없는 LOT 표기 — 매칭 키를 만들 수 없다.
    _add_reading(client, pid, f"{code}-4", 330.0, "2026-09-06", material_lot="확인불가")
    # 사용한 PB 칸이 비어 있다.
    _add_reading(client, pid, f"{code}-5", 340.0, "2026-09-07")

    link = _analyze(client, pid)["pb_link"]
    assert link["source_code"] == "PB"
    assert link["source_exists"] is True
    assert link["is_source"] is False
    assert link["total"] == 5
    assert link["matched"] == 1
    assert link["pb_excluded"] == 1
    assert link["pb_missing"] == 1
    assert link["lot_unreadable"] == 1
    assert link["no_lot"] == 1
    # 계약: 사유의 합이 이 화면이 보고 있는 측정 건수와 같다.
    total = (
        link["matched"] + link["no_lot"] + link["lot_unreadable"]
        + link["pb_missing"] + link["pb_excluded"]
    )
    assert total == link["total"]
    # 구간표는 PB 반제품의 기준선을 쓰므로 함께 내려온다.
    assert set(link["source_limits"]) == {"lower_limit", "warn_low", "warn_high", "upper_limit"}


def test_pb_product_itself_reports_no_upstream_link():
    """PB 자신에는 위 단계 PB 가 없다 — 사유는 전부 0, is_source 로 화면이 갈린다."""
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    _add_reading(client, pb_id, f"PB{_digits8()}", 50.0, "2026-09-08")

    link = _analyze(client, pb_id)["pb_link"]
    assert link["is_source"] is True
    assert link["matched"] == 0
    assert (link["no_lot"], link["lot_unreadable"], link["pb_missing"], link["pb_excluded"]) == (0, 0, 0, 0)


# ── 2. PB LOT 으로 찾기(고르기 목록) ─────────────────────────────────
def test_pb_lot_picker_filters_by_lot_orders_by_measured_date_and_counts_links():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)

    token = f"{uuid.uuid4().int % 10**7:07d}"
    lots = [f"PB{token}{i}" for i in range(3)]
    _add_reading(client, pb_id, lots[0], 47.0, "2026-09-11")
    _add_reading(client, pb_id, lots[1], 48.5, "2026-09-13")
    _add_reading(client, pb_id, lots[2], 50.0, "2026-09-12")

    code = "PBP" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    # 가운데 LOT 으로 두 건을 만들었다 — 목록에 연계 건수로 보인다.
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-14", material_lot=f"{token}1")
    _add_reading(client, pid, f"{code}-2", 305.0, "2026-09-15", material_lot=f"{token}1")

    res = client.get("/api/viscosity/pb-lots", params={"q": token})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["source_code"] == "PB"
    assert data["total"] == 3
    assert [it["lot_no"] for it in data["items"]] == [lots[1], lots[2], lots[0]]
    by_lot = {it["lot_no"]: it for it in data["items"]}
    assert by_lot[lots[1]]["viscosity"] == 48.5
    assert by_lot[lots[1]]["measured_date"] == "2026-09-13"
    assert by_lot[lots[1]]["linked_count"] == 2
    assert by_lot[lots[0]]["linked_count"] == 0
    assert by_lot[lots[0]]["excluded"] is False

    # limit — 목록은 자르되 total 은 전체를 말한다.
    short = client.get("/api/viscosity/pb-lots", params={"q": token, "limit": 1}).json()
    assert short["total"] == 3
    assert [it["lot_no"] for it in short["items"]] == [lots[1]]
    assert short["limit"] == 1


def test_pb_lot_picker_marks_excluded_pb_readings():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    lot = f"PB{_digits8()}"
    reading_id = _add_reading(client, pb_id, lot, 41.0, "2026-09-16")
    _exclude(client, reading_id, "설비 점검 중 측정")

    items = client.get("/api/viscosity/pb-lots", params={"q": lot}).json()["items"]
    assert [it["lot_no"] for it in items] == [lot]
    assert items[0]["excluded"] is True


# ── 3. PB LOT 하나의 상세 ────────────────────────────────────────────
def test_pb_lot_detail_lists_binder_readings_with_verdicts():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    digits = _digits8()
    pb_lot = f"PB{digits}"
    _add_reading(client, pb_id, pb_lot, 49.5, "2026-09-17")

    # 관리 상한 320 — 이 반제품의 두 측정 중 하나는 이상이다.
    code = "PBD" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code, upper_limit=320.0)
    _add_reading(client, pid, f"{code}-1", 300.0, "2026-09-18", material_lot=digits)
    _add_reading(client, pid, f"{code}-2", 330.0, "2026-09-19", material_lot=digits)
    # 다른 PB LOT 을 쓴 측정은 섞이지 않는다.
    _add_reading(client, pid, f"{code}-3", 310.0, "2026-09-19", material_lot=_digits8())

    res = client.get(f"/api/viscosity/pb-lots/{pb_lot}")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["pb"]["lot_no"] == pb_lot
    assert data["pb"]["viscosity"] == 49.5
    assert data["pb"]["measured_date"] == "2026-09-17"
    assert data["pb"]["excluded"] is False
    assert data["limit"] == 50

    mine = [it for it in data["items"] if it["product_code"] == code]
    assert [it["lot_no"] for it in mine] == [f"{code}-2", f"{code}-1"]   # 최근 먼저
    assert mine[0]["status"] == "anomaly"
    assert "spec_high" in mine[0]["reasons"]
    assert mine[1]["status"] in ("normal", "warn")

    # 접두사가 다른 같은 LOT(숫자 8자리)도 같은 것으로 본다.
    same = client.get(f"/api/viscosity/pb-lots/{digits}").json()
    assert same["pb"]["lot_no"] == pb_lot
    assert {it["lot_no"] for it in same["items"]} >= {f"{code}-1", f"{code}-2"}


def test_pb_lot_detail_shows_test_blend_viscosity_as_reference_only():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    digits = _digits8()
    pb_lot = f"PB{digits}"
    _add_reading(client, pb_id, pb_lot, 48.8, "2026-09-17")

    # 시험 배합은 등록된 자재만 쓴다(계약 2차 결정) — 품목코드 없이 등록한다.
    names = ["연계시험A" + uuid.uuid4().hex[:5], "연계시험B" + uuid.uuid4().hex[:5]]
    mats = []
    for name in names:
        res = client.post("/api/materials", json={"name": name}, headers=_csrf(client))
        assert res.status_code == 200, res.text
        mats.append((int(res.json()["id"]), name))

    worker = "연계시험" + uuid.uuid4().hex[:5]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": worker}, headers=_csrf(client))
    test_name = "시험" + uuid.uuid4().hex[:5].upper()
    saved = client.post(
        "/api/blend/records",
        json={
            "is_test": True,
            "product_name": test_name,
            "worker": worker,
            "work_date": "2026-09-18",
            "total_amount": 1,
            "details": [
                {"material_id": mats[0][0], "material_name": mats[0][1],
                 "theory_amount": 30, "actual_amount": 30, "material_lot": digits},
                {"material_id": mats[1][0], "material_name": mats[1][1],
                 "theory_amount": 70, "actual_amount": 70, "material_lot": "X-1"},
            ],
        },
        headers=_csrf(client),
    )
    assert saved.status_code == 200, saved.text
    record = saved.json()
    reg = client.post(
        f"/api/viscosity/test-records/{record['id']}",
        json={"viscosity": 275.0, "memo": "1차"},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text

    data = client.get(f"/api/viscosity/pb-lots/{pb_lot}").json()
    tests = [t for t in data["tests"] if t["blend_record_id"] == record["id"]]
    assert len(tests) == 1
    assert tests[0]["viscosity"] == 275.0
    assert tests[0]["product_name"] == test_name
    assert tests[0]["product_lot"] == record["product_lot"]
    assert tests[0]["work_date"] == "2026-09-18"
    assert tests[0]["memo"] == "1차"
    # 시험 점도에는 판정을 붙이지 않는다(비교할 기준이 없다).
    assert "status" not in tests[0]
    # 시험 배합은 정식 측정 목록에 섞이지 않는다.
    assert all(it["lot_no"] != record["product_lot"] for it in data["items"])


def test_unknown_pb_lot_answers_empty_instead_of_404():
    """검색 중간 입력에 오류창이 뜨면 안 된다 — 빈 목록으로 답한다."""
    client = _client()
    _login_admin(client)
    _pb_product_id(client)
    res = client.get(f"/api/viscosity/pb-lots/PB{_digits8()}")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["pb"] is None
    assert data["items"] == []
    assert data["tests"] == []


def test_pb_lot_detail_caps_the_binder_reading_list():
    """한 PB LOT 에 붙은 측정이 아무리 많아도 상세는 50건까지만 싣는다."""
    from src.db import get_connection
    from src.services import viscosity_service as vs

    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    digits = _digits8()
    pb_lot = f"PB{digits}"
    _add_reading(client, pb_id, pb_lot, 49.0, "2026-09-17")

    code = "PBC" + uuid.uuid4().hex[:5].upper()
    pid = _make_product(client, code)
    # 51건은 HTTP 로 넣기엔 느리다 — 같은 서비스가 쓰는 DB 에 직접 적는다.
    with get_connection() as conn:
        for i in range(vs.PB_LOT_DETAIL_MAX + 1):
            conn.execute(
                "INSERT INTO viscosity_readings "
                "(product_id, lot_no, viscosity, measured_date, material_lot, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, f"{code}-{i:03d}", 300.0 + i, "2026-09-18", digits, "테스트", "2026-09-18T00:00:00Z"),
            )
        conn.commit()

    data = client.get(f"/api/viscosity/pb-lots/{pb_lot}").json()
    assert len(data["items"]) == vs.PB_LOT_DETAIL_MAX


# ── 4. 배합 화면의 managed 구분 ──────────────────────────────────────
def test_product_lot_alert_tells_unmanaged_material_from_missing_reading():
    client = _client()
    _login_admin(client)
    pb_id = _pb_product_id(client)
    lot = f"PB{_digits8()}"
    _add_reading(client, pb_id, lot, 49.9, "2026-09-20")

    # ① 점도와 무관한 일반 원료 — 배합 화면은 아무것도 띄우지 않는다.
    plain = client.get(
        "/api/blend/product-lot-viscosity",
        params={"name": "원료" + uuid.uuid4().hex[:6], "lot": "M-1"},
    ).json()
    assert plain["managed"] is False and plain["found"] is False

    # ② 점도를 재는 반제품인데 이 LOT 만 기록이 없다 — 조용한 안내 대상.
    missing = client.get(
        "/api/blend/product-lot-viscosity",
        params={"name": "PB", "lot": "PB" + _digits8()},
    ).json()
    assert missing["managed"] is True and missing["found"] is False
    assert missing["product"] == "PB"
    assert missing["level"] is None and missing["message"] is None

    # ③ 기록이 있으면 종전과 같다(판정·문구 불변).
    found = client.get(
        "/api/blend/product-lot-viscosity", params={"name": "PB", "lot": lot}
    ).json()
    assert found["managed"] is True and found["found"] is True
    assert found["viscosity"] == 49.9
