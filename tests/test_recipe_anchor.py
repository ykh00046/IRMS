"""기준 자재(anchor material) 지원 — 임포트·수정 등록 승계·PUT 지정·배합 환산.

반제품(PB 등) 배합에서 한 자재를 기준(anchor)으로 먼저 계량하고, 그 실측값으로
나머지 자재들의 이론량을 산출하기 위한 서버 지원. 본 테스트는 서버 동작만 검증한다.

검증 항목:
  (a) anchor_material 을 준 임포트 → recipes.anchor_material_id 저장
  (b) 임포트 항목에 없는 이름 → 400
  (c) 수정 등록(revision): 자재가 남아있으면 승계, 빠지면 NULL
  (d) PUT /recipes/{id}/anchor — 정상 / 400(레시피 구성 자재 아님) / 401(비책임자)
  (e) get_recipe_for_blend: is_anchor 플래그 + 방어적 None 케이스
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


def _import(client, headers, product, weights_map, anchor=None, revision_of=None, force=True):
    """weights_map: {자재이름: 중량}. 동일 헤더 한 줄 임포트."""
    names = list(weights_map.keys())
    header = "반제품명\t" + "\t".join(names)
    row = product + "\t" + "\t".join(str(weights_map[n]) for n in names)
    body: dict = {"raw_text": header + "\n" + row, "force": force}
    if anchor is not None:
        body["anchor_material"] = anchor
    if revision_of is not None:
        body["revision_of"] = revision_of
    res = client.post("/api/recipes/import", json=body, headers=headers)
    return res


# ── in-memory 서비스 헬퍼 ──────────────────────────────────────
def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, unit_type TEXT, unit TEXT DEFAULT 'g',
            category TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, position TEXT, ink_name TEXT,
            status TEXT DEFAULT 'completed', created_at TEXT DEFAULT '2026-01-01',
            revision_of INTEGER, base_total REAL, base_totals TEXT,
            anchor_material_id INTEGER
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER, material_id INTEGER,
            value_weight REAL, value_text TEXT
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL, sigma_k REAL DEFAULT 3,
            rpm REAL, temperature REAL, remind_daily INTEGER DEFAULT 0,
            use_reactor INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, created_at TEXT
        );
        """
    )
    return conn


def _seed_recipe(conn, product="ANCHOR1", weights=(60.0, 30.0, 10.0)):
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status) VALUES (?, ?, 'completed')",
        (product, product),
    ).lastrowid
    mids = []
    for i, w in enumerate(weights):
        mid = conn.execute(
            "INSERT INTO materials (name, unit_type, unit, category) VALUES (?, 'weight', 'g', ?)",
            (f"원료{i+1}", f"M00{i+1}"),
        ).lastrowid
        mids.append(mid)
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, ?)",
            (rid, mid, w),
        )
    return rid, mids


# ── (a) 임포트 시 anchor_material_id 저장 ───────────────────────
def test_import_with_anchor_material_sets_column():
    """anchor_material 을 지정한 임포트 → 일치 자재의 id 가 anchor_material_id 로 저장."""
    client = _client()
    headers = _login(client)
    product = f"PANCH{_uid()}"

    res = _import(client, headers, product, {"MatA": 60, "MatB": 40}, anchor="MatA")
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]

    from src.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT r.anchor_material_id, m.name
            FROM recipes r LEFT JOIN materials m ON m.id = r.anchor_material_id
            WHERE r.id = ?
            """,
            (rid,),
        ).fetchone()
    assert row["name"] == "MatA"
    assert row["anchor_material_id"] is not None


# ── (b) 임포트 항목에 없는 anchor_material → 400 ────────────────
def test_import_with_unknown_anchor_material_returns_400():
    """anchor_material 이 임포트 항목 중 어느 자재 이름과도 일치하지 않으면 400."""
    client = _client()
    headers = _login(client)
    product = f"PUNK{_uid()}"

    res = _import(client, headers, product, {"MatA": 60, "MatB": 40}, anchor="없는자재")
    assert res.status_code == 400, res.text
    # 한국어 에러 메시지
    detail = res.json().get("detail")
    if isinstance(detail, dict):
        msg = detail.get("message", "")
    else:
        msg = str(detail)
    assert "기준 자재" in msg


# ── (c) 수정 등록 승계: 남아있으면 승계, 빠지면 NULL ────────────
def test_revision_inherits_anchor_when_present_drops_when_absent():
    """수정 등록 시 anchor_material 미지정 → 부모 anchor 가 새 버전 자재에 있으면 승계,
    빠지면 NULL. base_totals 승계 구조와 동일."""
    client = _client()
    headers = _login(client)
    product = f"PREE{_uid()}"

    from src.db import get_connection

    # 1) 원본을 MatA 기준으로 임포트
    base_id = _import(
        client, headers, product, {"MatA": 60, "MatB": 40}, anchor="MatA"
    ).json()["created_ids"][0]
    with get_connection() as conn:
        assert conn.execute(
            "SELECT anchor_material_id FROM recipes WHERE id=?", (base_id,)
        ).fetchone()[0] is not None

    # 2) 개정 — MatA 유지 → anchor 승계
    rev_keep = _import(
        client, headers, product, {"MatA": 70, "MatB": 30}, revision_of=base_id
    ).json()["created_ids"][0]
    with get_connection() as conn:
        # 부모의 anchor_material_id 와 동일한 값을 승계했는지
        parent_anchor, rev_anchor = conn.execute(
            "SELECT (SELECT anchor_material_id FROM recipes WHERE id=?) AS p, "
            "anchor_material_id AS r FROM recipes WHERE id=?",
            (base_id, rev_keep),
        ).fetchone()
    assert rev_anchor == parent_anchor  # 승계
    assert rev_anchor is not None

    # 3) 개정 — MatA 제거(새 자재로 교체) → anchor 새 항목에 없으므로 NULL
    rev_drop = _import(
        client, headers, product, {"MatC": 50, "MatB": 50}, revision_of=base_id
    ).json()["created_ids"][0]
    with get_connection() as conn:
        assert conn.execute(
            "SELECT anchor_material_id FROM recipes WHERE id=?", (rev_drop,)
        ).fetchone()[0] is None


# ── (d) PUT /recipes/{id}/anchor ────────────────────────────────
def test_put_anchor_happy_path_as_manager():
    """책임자가 PUT /recipes/{id}/anchor 로 기준 자재를 지정 → anchor_material_id 갱신."""
    client = _client()
    headers = _login(client)
    product = f"PPUT{_uid()}"
    rid = _import(client, headers, product, {"MatA": 60, "MatB": 40}).json()["created_ids"][0]

    from src.db import get_connection

    with get_connection() as conn:
        matb_id = conn.execute(
            "SELECT m.id FROM materials m JOIN recipe_items ri ON ri.material_id = m.id "
            "WHERE ri.recipe_id = ? AND m.name = 'MatB'",
            (rid,),
        ).fetchone()[0]

    res = client.put(
        f"/api/recipes/{rid}/anchor",
        json={"material_id": matb_id},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["anchor_material_id"] == matb_id

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT anchor_material_id FROM recipes WHERE id=?", (rid,)
        ).fetchone()[0]
    assert stored == matb_id

    # None 으로 해제
    res2 = client.put(
        f"/api/recipes/{rid}/anchor", json={"material_id": None}, headers=headers
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["anchor_material_id"] is None


def test_put_anchor_400_when_material_not_in_recipe():
    """레시피 구성 자재가 아닌 material_id → 400(한국어 메시지)."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"PNM{_uid()}", {"MatA": 60, "MatB": 40}).json()["created_ids"][0]

    from src.db import get_connection

    # 레시피에 속하지 않는 자재 id 확보(다른 자재 생성, 이름 유니크)
    with get_connection() as conn:
        outsider = conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active) "
            "VALUES (?, 'weight', 'g', 'none', '기타', 1)",
            (f"외부자재{_uid()}",),
        ).lastrowid
        conn.commit()

    res = client.put(
        f"/api/recipes/{rid}/anchor",
        json={"material_id": outsider},
        headers=headers,
    )
    assert res.status_code == 400, res.text


def test_put_anchor_401_without_manager_session():
    """비책임자(미인증) 요청 → 401/403. require_access_level('manager') 가 좌우."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"PNLOG{_uid()}", {"MatA": 60, "MatB": 40}).json()["created_ids"][0]

    from src.db import get_connection

    with get_connection() as conn:
        mat_id = conn.execute(
            "SELECT material_id FROM recipe_items WHERE recipe_id = ? LIMIT 1", (rid,)
        ).fetchone()[0]

    # 헤더 없이(미인증) PUT → manager 권한 게이트에서 거부(401 또는 403)
    res = client.put(f"/api/recipes/{rid}/anchor", json={"material_id": mat_id})
    assert res.status_code in (401, 403), res.text


# ── (e) get_recipe_for_blend: is_anchor + 방어적 None ───────────
def test_get_recipe_for_blend_marks_is_anchor_flag():
    """get_recipe_for_blend: 기준 자재 항목만 is_anchor=True, 나머지는 False.
    anchor_material_id 가 레시피 dict 에 포함."""
    conn = _make_db()
    rid, mids = _seed_recipe(conn, weights=(60.0, 30.0, 10.0))
    anchor_id = mids[1]  # 원료2
    conn.execute("UPDATE recipes SET anchor_material_id = ? WHERE id = ?", (anchor_id, rid))

    result = bs.get_recipe_for_blend(conn, rid)
    assert result is not None
    assert result["recipe"]["anchor_material_id"] == anchor_id
    flags = {it["material_id"]: it["is_anchor"] for it in result["items"]}
    assert flags[anchor_id] is True
    # 나머지는 모두 False
    for mid, is_anchor in flags.items():
        if mid != anchor_id:
            assert is_anchor is False


def test_get_recipe_for_blend_defensive_none_when_anchor_missing_or_zero():
    """방어: anchor_material_id 가 (1) 항목에 없거나 (2) 중량이 0 이하이면
    anchor_material_id=None, 모든 is_anchor=False 로 반환."""
    conn = _make_db()

    # 케이스 1: anchor 가 이 레시피 항목이 아닌 자재를 가리킴
    rid1, mids1 = _seed_recipe(conn, product="MISS", weights=(60.0, 30.0, 10.0))
    # 존재하지 않는 material_id
    conn.execute("UPDATE recipes SET anchor_material_id = 999999 WHERE id = ?", (rid1,))
    result1 = bs.get_recipe_for_blend(conn, rid1)
    assert result1["recipe"]["anchor_material_id"] is None
    assert all(it["is_anchor"] is False for it in result1["items"])

    # 케이스 2: anchor 자재의 value_weight 가 0
    rid2, mids2 = _seed_recipe(conn, product="ZERO", weights=(0.0, 30.0, 10.0))
    conn.execute(
        "UPDATE recipes SET anchor_material_id = ? WHERE id = ?", (mids2[0], rid2)
    )
    result2 = bs.get_recipe_for_blend(conn, rid2)
    assert result2["recipe"]["anchor_material_id"] is None
    assert all(it["is_anchor"] is False for it in result2["items"])


# ── (f) 상세(detail) 응답에 기준 자재 필드 포함 (관리 화면 표시·수정 등록용) ──
def test_recipe_detail_returns_anchor_fields():
    """GET /recipes/{id}/detail 은 anchor_material_id 와 anchor_material_name(조인) 을 반환.
    미지정 레시피는 둘 다 null."""
    client = _client()
    headers = _login(client)
    product = f"PDETAIL{_uid()}"
    rid = _import(
        client, headers, product, {"MatA": 60, "MatB": 40}, anchor="MatA"
    ).json()["created_ids"][0]

    detail = client.get(f"/api/recipes/{rid}/detail").json()
    # anchor 지정 → id 와 자재 이름 모두 반환
    assert detail["anchor_material_name"] == "MatA"
    assert detail["anchor_material_id"] is not None

    # 기준 자재 해제(PUT) 후에는 둘 다 null
    res = client.put(
        f"/api/recipes/{rid}/anchor", json={"material_id": None}, headers=headers
    )
    assert res.status_code == 200, res.text
    detail2 = client.get(f"/api/recipes/{rid}/detail").json()
    assert detail2["anchor_material_id"] is None
    assert detail2["anchor_material_name"] is None

