"""원료 반제품 연계(2026-10-06) — 원료를 PB 고정에서 레시피 도출로.

S-TOP·6-1 TOP 은 SBCT 로 만든다. 종전에는 원료가 PB 하나로 고정돼 이 품목들의 측정이
전부 '연계 없음'이었고, 등록 때 SBCT LOT 도 저장되지 않았다. 또 SBCT 의 2026 임포트 LOT
은 날짜만 있는 6자리(260513)라 자재 LOT(SBCT26051301)과 숫자 8자리로는 만나지 못했다.

  a. SBCT → 6-1 TOP: 등록 시 원료 LOT·원료 표기 저장, 분석의 source_link, 원료 목록,
     원료 LOT 목록·상세(날짜 LOT 매칭).
  b. 원료가 없는 반제품(NPR-S) — source_code 없음, 사유 건수 전부 0.
  c. 기동 마이그레이션이 원료 표기를 채운다(바인더 행 → PB, PB 자신의 행은 그대로).
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


def _csrf(client) -> dict:
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _login_admin(client):
    client.get("/api/blend/records")  # csrf 쿠키
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _seed_recipe(client, product, materials):
    lines = ["반제품명\t" + "\t".join(materials), product + "\t" + "\t".join(["50"] * len(materials))]
    res = client.post(
        "/api/recipes/import",
        json={"raw_text": "\n".join(lines), "force": True},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text


def _product_id(client, code) -> int:
    """반제품 id — 없으면 만든다(SBCT·PB 는 기동 시드로 이미 있다)."""
    for item in client.get("/api/viscosity/products").json()["items"]:
        if item["code"] == code:
            return int(item["id"])
    created = client.post(
        "/api/viscosity/products", json={"code": code, "name": code}, headers=_csrf(client)
    )
    assert created.status_code in (200, 201), created.text
    return int(created.json()["id"])


def _add_reading(client, product_id, lot, value, date, *, material_lot=None) -> None:
    body = {"product_id": product_id, "lot_no": lot, "viscosity": value, "measured_date": date}
    if material_lot is not None:
        body["material_lot"] = material_lot
    res = client.post("/api/viscosity/readings", json=body, headers=_csrf(client))
    assert res.status_code == 200, res.text


def _make_blend(client, product, work_date, details) -> int:
    """details: [(자재명, LOT)] — 계량 순서 그대로."""
    worker = "원료연계" + uuid.uuid4().hex[:4]
    client.post("/api/workers", json={"name": worker}, headers=_csrf(client))
    client.post("/api/blend/session/login", json={"worker": worker}, headers=_csrf(client))
    body = {
        "product_name": product, "worker": worker, "work_date": work_date,
        "total_amount": 100, "scale": "M-65",
        "details": [
            {"material_name": name, "ratio": 100 / len(details),
             "theory_amount": 100 / len(details),
             "actual_amount": 100 / len(details), "material_lot": lot}
            for name, lot in details
        ],
    }
    res = client.post("/api/blend/records", json=body, headers=_csrf(client))
    assert res.status_code == 200, res.text
    return int(res.json()["id"])


# ── a. SBCT → 6-1 TOP ────────────────────────────────────────────────
def test_sbct_source_links_6_1_top_end_to_end():
    from src.db import get_connection

    client = _client()
    _login_admin(client)
    sbct_id = _product_id(client, "SBCT")
    # 2026 임포트 형식(날짜만) 과 배합 화면 형식(접두사+8자리) 두 가지 SBCT LOT.
    _add_reading(client, sbct_id, "260513", 204.0, "2026-05-13")
    _add_reading(client, sbct_id, "SBCT26060901", 211.0, "2026-06-09")

    # 다른 테스트 파일(반제품 코드 교정)이 같은 DB 에 "6-1 TOP" 측정을 남긴다 — 이름을 유일하게.
    top = "6-1 TOP " + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, top, ("Miramer PU622", "SBCT", "MEK"))
    top_id = _product_id(client, top)
    # PB 로 만드는 바인더도 하나 — 원료 목록에 PB 가 함께 나와야 한다.
    binder = "SRA" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, binder, ("PB", "MEK"))
    _product_id(client, binder)

    rid = _make_blend(
        client, top, "2026-05-14",
        [("Miramer PU622", "MP-1"), ("SBCT", "SBCT26051301"), ("MEK", "MK-1")],
    )
    preview = client.get(f"/api/viscosity/blend-records/{rid}/used-source").json()
    assert preview == {
        "source_code": "SBCT", "lot": "SBCT26051301", "method": "matched",
        "source_viscosity": 204.0,
    }

    reg = client.post(
        f"/api/blend/records/{rid}/viscosity",
        json={"viscosity": 95.0, "product_id": top_id},
        headers=_csrf(client),
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["used_source"] == {
        "source_code": "SBCT", "lot": "SBCT26051301", "method": "matched",
    }
    with get_connection() as conn:
        row = conn.execute(
            "SELECT material_lot, source_code FROM viscosity_readings WHERE blend_record_id = ?",
            (rid,),
        ).fetchone()
    assert row["material_lot"] == "SBCT26051301"
    assert row["source_code"] == "SBCT"

    detail = client.get(f"/api/viscosity/products/{top_id}").json()
    link = detail["source_link"]
    assert link["source_code"] == "SBCT"
    assert link["source_name"] == "SBCT"
    assert link["source_exists"] is True
    assert link["is_source"] is False
    assert link["matched"] == 1
    assert (link["no_lot"], link["lot_unreadable"], link["source_missing"],
            link["source_excluded"]) == (0, 0, 0, 0)
    reading = next(r for r in detail["readings"] if r["viscosity"] == 95.0)
    assert reading["source_viscosity"] == 204.0       # 날짜 LOT 260513 의 점도
    assert reading["source_code"] == "SBCT"

    # SBCT 쪽에서 보면 원료다.
    sbct_link = client.get(f"/api/viscosity/products/{sbct_id}").json()["source_link"]
    assert sbct_link["is_source"] is True

    sources = {it["code"]: it for it in client.get("/api/viscosity/sources").json()["items"]}
    assert top in sources["SBCT"]["used_by"]
    assert binder in sources["PB"]["used_by"]
    assert set(sources["SBCT"]) == {"id", "code", "name", "used_by"}

    lots = client.get(
        "/api/viscosity/source-lots", params={"source": "SBCT", "limit": 100}
    ).json()
    assert lots["source_code"] == "SBCT"
    by_lot = {it["lot_no"]: it for it in lots["items"]}
    assert by_lot["260513"]["linked_count"] == 1
    assert by_lot["SBCT26060901"]["linked_count"] == 0

    found = client.get(
        "/api/viscosity/source-lots/SBCT26051301", params={"source": "SBCT"}
    ).json()
    assert found["source_code"] == "SBCT"
    assert found["source"]["lot_no"] == "260513"      # 날짜로 찾았다
    assert found["source"]["viscosity"] == 204.0
    assert [it["product_code"] for it in found["items"]] == [top]
    assert found["items"][0]["material_lot"] == "SBCT26051301"

    # 같은 날 다른 8자리 배치는 날짜만으로 잇지 않는다.
    other = client.get(
        "/api/viscosity/source-lots/SBCT26060902", params={"source": "SBCT"}
    ).json()
    assert other["source"] is None

    # 원료 미지정·모르는 원료는 빈 답(404 아님).
    assert client.get("/api/viscosity/source-lots").json()["items"] == []
    unknown = client.get(
        "/api/viscosity/source-lots/260513", params={"source": "없는원료"}
    ).json()
    assert unknown["source"] is None and unknown["items"] == []


# ── b. 원료가 없는 반제품 ─────────────────────────────────────────────
def test_product_without_source_reports_no_link_and_zero_reasons():
    client = _client()
    _login_admin(client)
    _seed_recipe(client, "NPR-S", ("MEK", "TOL"))
    pid = _product_id(client, "NPR-S")
    _add_reading(client, pid, "NPRS-" + uuid.uuid4().hex[:6], 120.0, "2026-05-20",
                 material_lot="26052001")
    _add_reading(client, pid, "NPRS-" + uuid.uuid4().hex[:6], 121.0, "2026-05-21")

    detail = client.get(f"/api/viscosity/products/{pid}").json()
    link = detail["source_link"]
    assert link["source_code"] is None
    assert link["source_name"] is None
    assert link["source_exists"] is False
    assert link["matched"] == 0
    assert (link["no_lot"], link["lot_unreadable"], link["source_missing"],
            link["source_excluded"]) == (0, 0, 0, 0)
    assert set(link["source_limits"].values()) == {None}
    assert all(r["source_viscosity"] is None for r in detail["readings"])


# ── c. 기동 마이그레이션의 원료 표기 채우기 ─────────────────────────────
def test_migration_backfills_source_code_from_recipe():
    from src.db import get_connection
    from src.db.migrations import apply_schema_migrations

    client = _client()
    _login_admin(client)
    pb_id = _product_id(client, "PB")
    binder = "SRB" + uuid.uuid4().hex[:5].upper()
    _seed_recipe(client, binder, ("PB", "MEK"))
    binder_id = _product_id(client, binder)
    pb_lot = "PB" + uuid.uuid4().hex[:6].upper()

    with get_connection() as conn:
        # 원료 표기 없이 들어간 옛 행 두 가지(바인더 행 + PB 자신의 행).
        for product_id, lot in ((binder_id, f"{binder}-1"), (pb_id, pb_lot)):
            conn.execute(
                "INSERT INTO viscosity_readings (product_id, lot_no, viscosity, measured_date, "
                "material_lot, created_by, created_at) VALUES (?, ?, 300, '2026-06-01', "
                "'26060101', 't', '2026-06-01T00:00:00Z')",
                (product_id, lot),
            )
        conn.commit()
        apply_schema_migrations(conn)
        conn.commit()
        rows = {
            r["lot_no"]: r["source_code"]
            for r in conn.execute(
                "SELECT lot_no, source_code FROM viscosity_readings WHERE lot_no IN (?, ?)",
                (f"{binder}-1", pb_lot),
            ).fetchall()
        }
    assert rows[f"{binder}-1"] == "PB"
    assert rows[pb_lot] is None
