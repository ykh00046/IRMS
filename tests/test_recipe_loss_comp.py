"""투입 로스 보정(파우더 투입 손실 보정) 1라운드 — 레시피 지정·승계·배합 적용.

일부 파우더 자재는 붓는 과정 로스 때문에 지정량(예: +1g) 더 계량하는 게 공정 기준.
레시피 아이템별 loss_comp_g(고정 g) 로 저장하고, 배합 저장 시
blend_details.theory_amount = round(비율×총량, 2) + loss_comp_g 가 된다.
보정량은 메타(blend_details.loss_comp_g)로도 스냅샷. 기록·출력엔 보정 포함량이 그대로.

검증 항목:
  (a) PUT /recipes/{id}/loss-comp 지정 → recipe_items.loss_comp_g 저장 + 개정 승계
  (b) 보정 자재 배합 저장: theory = 비율환산+보정. actual=그 값이면 통과, 미달이면 400
  (c) GET /blend/recipes/{id}?total= 환산 이론량에 보정 포함 + loss_comp_g 필드 병기
  (d) GET /blend/records/{id} 상세 detail 에 loss_comp_g 스냅샷 포함
  (e) PUT 검증: BOM 외 자재 400 / 음수·100 초과 400
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.services import blend_service as bs


# ── TestClient(라우트) 헬퍼 ────────────────────────────────────
def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client):
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _import(client, headers, product, weights_map, revision_of=None, force=True):
    """weights_map: {자재이름: 중량}. 동일 헤더 한 줄 임포트."""
    names = list(weights_map.keys())
    header = "반제품명\t" + "\t".join(names)
    row = product + "\t" + "\t".join(str(weights_map[n]) for n in names)
    body: dict = {"raw_text": header + "\n" + row, "force": force}
    if revision_of is not None:
        body["revision_of"] = revision_of
    return client.post("/api/recipes/import", json=body, headers=headers)


# ── (a) PUT 지정 → 저장 + 개정 승계 ─────────────────────────────
def test_put_loss_comp_persists_and_detail_exposes():
    """PUT /recipes/{id}/loss-comp 로 보정 지정 → recipe_items.loss_comp_g 저장,
    GET /recipes/{id}/detail 와 /blend/recipes/{id} 모두 loss_comp_g 필드 노출."""
    client = _client()
    headers = _login(client)
    product = f"PLC{_uid()}"
    rid = _import(client, headers, product, {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]

    res = client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": 1.0}]},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT ri.loss_comp_g FROM recipe_items ri
            JOIN materials m ON m.id = ri.material_id
            WHERE ri.recipe_id = ? AND m.name = ?
            """,
            (rid, "PowderA"),
        ).fetchone()
        assert float(row["loss_comp_g"]) == 1.0

    # 관리 상세에 loss_comp_g 노출
    detail = client.get(f"/api/recipes/{rid}/detail").json()
    items = {it["material_name"]: it for it in detail["items"]}
    assert items["PowderA"]["loss_comp_g"] == 1.0
    assert items["LiquidB"]["loss_comp_g"] == 0.0

    # 배합 화면용 GET 에도 loss_comp_g 필드 병기
    blend_recipe = client.get(f"/api/blend/recipes/{rid}").json()
    bitems = {it["material_name"]: it for it in blend_recipe["items"]}
    assert bitems["PowderA"]["loss_comp_g"] == 1.0


def test_revision_inherits_loss_comp():
    """수정 등록(revision_of) 시 부모의 자재별 loss_comp_g 를 같은 이름으로 승계
    (anchor_material·tolerance_g 승계와 같은 자리)."""
    client = _client()
    headers = _login(client)
    product = f"PLCREE{_uid()}"
    base_id = _import(client, headers, product, {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]
    client.put(
        f"/api/recipes/{base_id}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": 2.0}]},
        headers=headers,
    )

    rev_id = _import(
        client, headers, product, {"PowderA": 70, "LiquidB": 30}, revision_of=base_id
    ).json()["created_ids"][0]

    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT ri.loss_comp_g FROM recipe_items ri
            JOIN materials m ON m.id = ri.material_id
            WHERE ri.recipe_id = ? AND m.name = ?
            """,
            (rev_id, "PowderA"),
        ).fetchone()
        assert float(row["loss_comp_g"]) == 2.0, "개정이 부모의 보정값을 승계해야 한다"


# ── (b) 배합 저장: theory = 비율환산+보정 ───────────────────────
def test_blend_save_theory_includes_loss_comp_pass_and_fail():
    """보정 자재의 배합 저장 시 theory_amount = 비율×총량+보정.
    actual=그 값이면 편차 통과(200), 보정분만큼 미달이면 400."""
    client = _client()
    headers = _login(client)
    worker = "보정작업" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)

    # PowderA 60 / LiquidB 40, 총량 100 → PowderA 이론 60, 보정 +1 → 목표 61
    product = f"PLCB{_uid()}"
    rid = _import(client, headers, product, {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]
    client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": 1.0}]},
        headers=headers,
    )

    # 1) actual = 보정 포함 목표(61 / 40) → 저장 성공
    ok_payload = {
        "recipe_id": rid,
        "product_name": product,
        "worker": worker,
        "work_date": "2026-08-05",
        "total_amount": 100.0,
        "details": [
            {"material_name": "PowderA", "actual_amount": 61.0, "material_lot": "LP"},
            {"material_name": "LiquidB", "actual_amount": 40.0, "material_lot": "LB"},
        ],
    }
    saved = client.post("/api/blend/records", json=ok_payload, headers=headers)
    assert saved.status_code == 200, saved.text
    # 저장된 blend_details.theory_amount 가 보정 포함(61) 인지, loss_comp_g 스냅샷도 1.0 인지.
    from src.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT material_name, theory_amount, loss_comp_g FROM blend_details
            WHERE blend_record_id = ? ORDER BY sequence_order
            """,
            (saved.json()["id"],),
        ).fetchall()
    by_name = {r["material_name"]: r for r in rows}
    assert float(by_name["PowderA"]["theory_amount"]) == 61.0, "보정 포함 이론량(61) 이어야 한다"
    assert float(by_name["PowderA"]["loss_comp_g"]) == 1.0, "보정 스냅샷이 저장돼야 한다"
    assert float(by_name["LiquidB"]["theory_amount"]) == 40.0

    # 2) actual = 보정 미포함(60 / 40) → 1g 부족 → 400
    short_payload = {
        "recipe_id": rid,
        "product_name": product,
        "worker": worker,
        "work_date": "2026-08-05",
        "total_amount": 100.0,
        "details": [
            {"material_name": "PowderA", "actual_amount": 60.0, "material_lot": "LP2"},
            {"material_name": "LiquidB", "actual_amount": 40.0, "material_lot": "LB2"},
        ],
        "request_id": "loss-comp-short-" + uuid.uuid4().hex[:8],
    }
    rejected = client.post("/api/blend/records", json=short_payload, headers=headers)
    assert rejected.status_code == 400, rejected.text


# ── (c) GET /blend/recipes/{id}?total= 환산에 보정 포함 ─────────
def test_get_recipe_for_blend_total_includes_loss_comp():
    """?total= 환산 이론량 = 비율×총량+보정. loss_comp_g 필드도 병기."""
    client = _client()
    headers = _login(client)
    product = f"PLCC{_uid()}"
    rid = _import(client, headers, product, {"PowderA": 50, "LiquidB": 50}).json()["created_ids"][0]
    client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": 1.5}]},
        headers=headers,
    )

    # 총량 200 → PowderA 비율환산 100 + 보정 1.5 = 101.5
    res = client.get(f"/api/blend/recipes/{rid}?total=200").json()
    items = {it["material_name"]: it for it in res["items"]}
    assert items["PowderA"]["theory_amount"] == 101.5
    assert items["PowderA"]["loss_comp_g"] == 1.5
    assert items["LiquidB"]["theory_amount"] == 100.0


# ── (d) 상세 detail 에 loss_comp_g 스냅샷 ───────────────────────
def test_blend_record_detail_includes_loss_comp_g():
    """GET /blend/records/{id} 의 각 detail 에 loss_comp_g 스냅샷 포함."""
    client = _client()
    headers = _login(client)
    worker = "보정상세" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)

    product = f"PLCD{_uid()}"
    rid = _import(client, headers, product, {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]
    client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": 1.0}]},
        headers=headers,
    )
    saved = client.post(
        "/api/blend/records",
        json={
            "recipe_id": rid, "product_name": product, "worker": worker, "work_date": "2026-08-05",
            "total_amount": 100.0,
            "details": [
                {"material_name": "PowderA", "actual_amount": 61.0, "material_lot": "LP"},
                {"material_name": "LiquidB", "actual_amount": 40.0, "material_lot": "LB"},
            ],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text

    detail = client.get(f"/api/blend/records/{saved.json()['id']}").json()
    by_name = {d["material_name"]: d for d in detail["details"]}
    assert by_name["PowderA"]["loss_comp_g"] == 1.0
    assert by_name["LiquidB"]["loss_comp_g"] == 0.0


# ── (e) PUT 검증 ────────────────────────────────────────────────
def test_put_loss_comp_400_for_non_bom_material():
    """BOM 에 없는 자재명 → 400."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"PLCE{_uid()}", {"PowderA": 60, "LiquidB": 40}).json()["created_ids"][0]
    res = client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "GhostZ", "loss_comp_g": 1.0}]},
        headers=headers,
    )
    assert res.status_code == 400, res.text
    assert "BOM" in res.json().get("detail", "")


def test_put_loss_comp_400_for_negative_or_over_limit():
    """음수 또는 100 초과 → 400."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"PLCF{_uid()}", {"PowderA": 60}).json()["created_ids"][0]
    res_neg = client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": -1.0}]},
        headers=headers,
    )
    assert res_neg.status_code == 400, res_neg.text
    res_hi = client.put(
        f"/api/recipes/{rid}/loss-comp",
        json={"items": [{"material_name": "PowderA", "loss_comp_g": 101.0}]},
        headers=headers,
    )
    assert res_hi.status_code == 400, res_hi.text


# ── 서비스 직접(in-memory) — 보정 0 경로 기존 동작 유지 ─────────
def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, unit_type TEXT, unit TEXT DEFAULT 'g',
            category TEXT, code TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, position TEXT, ink_name TEXT,
            status TEXT DEFAULT 'completed', created_at TEXT DEFAULT '2026-01-01',
            revision_of INTEGER, base_total REAL, base_totals TEXT,
            anchor_material_id INTEGER, tolerance_g REAL
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER, material_id INTEGER,
            value_weight REAL, value_text TEXT, loss_comp_g REAL DEFAULT 0
        );
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL, recipe_id INTEGER, product_name TEXT NOT NULL,
            ink_name TEXT, position TEXT, worker TEXT NOT NULL, work_date TEXT NOT NULL,
            work_time TEXT, total_amount REAL NOT NULL, scale TEXT,
            status TEXT NOT NULL DEFAULT 'completed', note TEXT, reactor INTEGER,
            manual_entry INTEGER NOT NULL DEFAULT 0, is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
            created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER, material_id INTEGER, material_code TEXT,
            material_name TEXT NOT NULL, material_lot TEXT, ratio REAL,
            theory_amount REAL, actual_amount REAL, sequence_order INTEGER DEFAULT 0,
            manual_entry INTEGER DEFAULT 0, carried_over INTEGER DEFAULT 0,
            loss_comp_g REAL DEFAULT 0, created_at TEXT
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            use_reactor INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1
        );
        """
    )
    return conn


def test_loss_comp_zero_keeps_existing_theory_contract():
    """보정 0 이면 이론량·정밀도가 기존과 동일(회귀 방지)."""
    conn = _make_db()
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status) VALUES ('LC0', 'LC0', 'completed')"
    ).lastrowid
    for i, w in enumerate((60.0, 40.0)):
        mid = conn.execute(
            "INSERT INTO materials (name, unit_type, unit) VALUES (?, 'weight', 'g')",
            (f"원료{i+1}",),
        ).lastrowid
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight, loss_comp_g) VALUES (?, ?, ?, 0)",
            (rid, mid, w),
        )
    result = bs.get_recipe_for_blend(conn, rid, total_amount=100.0)
    items = {it["material_name"]: it for it in result["items"]}
    # 보정 0 → 비율환산 그대로(60 / 40)
    assert items["원료1"]["theory_amount"] == 60.0
    assert items["원료2"]["theory_amount"] == 40.0
    assert items["원료1"]["loss_comp_g"] == 0.0
