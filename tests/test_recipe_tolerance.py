"""레시피별 계량 허용 편차(tolerance_g) — 임포트·승계·PUT 지정·배합 적용.

기본 허용 편차(0.05g)는 저울(A&D GX-10202M) 기준이었으나, 0.1g 분해능 저울
(CAS CBX-22KH) 로는 큰 배치에서 만족 불가. 각 레시피가 자체 편차를 가질 수 있게
한다. 기존 레시피는 NULL → 기본값 0.05g 으로 동작 불변(하위호환).

검증 항목:
  (a) tolerance_g NULL → recipe_tolerance_g/get_recipe_for_blend 기본값 0.05
  (b) 임포트 시 tolerance_g 지정 → recipes.tolerance_g 저장
  (c) 임포트 시 범위 초과 tolerance_g → 400
  (d) 수정 등록(revision): 미지정 시 부모 tolerance_g 승계
  (e) PUT /recipes/{id}/tolerance — 정상 / 400(범위 초과) / 401·403(비책임자) / null 해제
  (f) recipe_tolerance_g: 알 수 없는/None recipe_id → 기본값 폴백
  (g) 배합 실적 저장: 0.3g 편차가 레시피 편차 0.5g 이면 저장, 기본값 0.05g 이면 400
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


def _import(
    client,
    headers,
    product,
    weights_map,
    tolerance_g=None,
    revision_of=None,
    force=True,
):
    """weights_map: {자재이름: 중량}. 동일 헤더 한 줄 임포트."""
    names = list(weights_map.keys())
    header = "반제품명\t" + "\t".join(names)
    row = product + "\t" + "\t".join(str(weights_map[n]) for n in names)
    body: dict = {"raw_text": header + "\n" + row, "force": force}
    if tolerance_g is not None:
        body["tolerance_g"] = tolerance_g
    if revision_of is not None:
        body["revision_of"] = revision_of
    return client.post("/api/recipes/import", json=body, headers=headers)


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
            anchor_material_id INTEGER, tolerance_g REAL
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


def _seed_recipe(conn, product="TOL1", weights=(60.0, 30.0, 10.0)):
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status) VALUES (?, ?, 'completed')",
        (product, product),
    ).lastrowid
    for i, w in enumerate(weights):
        mid = conn.execute(
            "INSERT INTO materials (name, unit_type, unit, category) VALUES (?, 'weight', 'g', ?)",
            (f"원료{i+1}", f"M00{i+1}"),
        ).lastrowid
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, ?)",
            (rid, mid, w),
        )
    return rid


# ── (a) tolerance_g NULL → 기본값 0.05 ─────────────────────────
def test_default_tolerance_when_null():
    """tolerance_g 가 NULL 이면 recipe_tolerance_g 와 get_recipe_for_blend 모두 0.05."""
    conn = _make_db()
    rid = _seed_recipe(conn)
    assert bs.recipe_tolerance_g(conn, rid) == bs.WEIGHING_TOLERANCE_G
    result = bs.get_recipe_for_blend(conn, rid)
    assert result is not None
    assert result["recipe"]["tolerance_g"] == bs.WEIGHING_TOLERANCE_G


def test_weighing_tolerance_violations_default_single_arg_unchanged():
    """기존 단일 인수 호출은 기본값(0.05) 로 동작 — 하위호환."""
    details = [
        {"material_name": "A", "theory_amount": 10.0, "actual_amount": 10.04},  # OK
        {"material_name": "B", "theory_amount": 10.0, "actual_amount": 10.10},  # 위반
    ]
    # 단일 인수 — 기본 0.05
    assert "B" in bs.weighing_tolerance_violations(details)
    assert "A" not in bs.weighing_tolerance_violations(details)
    # 편차 인수로 0.5 를 주면 B 도 허용
    assert bs.weighing_tolerance_violations(details, tolerance_g=0.5) == []


# ── (b) 임포트 시 tolerance_g 저장 ─────────────────────────────
def test_import_with_tolerance_persists():
    """tolerance_g 를 지정한 임포트 → recipes.tolerance_g 저장."""
    client = _client()
    headers = _login(client)
    product = f"PTOL{_uid()}"

    res = _import(client, headers, product, {"MatA": 60, "MatB": 40}, tolerance_g=0.5)
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]

    from src.db import get_connection

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT tolerance_g FROM recipes WHERE id=?", (rid,)
        ).fetchone()[0]
    assert stored == 0.5


# ── (c) 임포트 시 범위 초과 tolerance_g → 400 ──────────────────
def test_import_with_out_of_range_tolerance_returns_400():
    """tolerance_g 가 0 이하이거나 1000 초과 → 400(또는 pydantic 422). 한국어 메시지 포함."""
    client = _client()
    headers = _login(client)
    product = f"PBAD{_uid()}"

    # 0 이하
    res_lo = _import(client, headers, product, {"MatA": 60, "MatB": 40}, tolerance_g=0)
    assert res_lo.status_code in (400, 422), res_lo.text
    assert "허용 편차" in res_lo.text

    # 1000 초과
    res_hi = _import(client, headers, product + "2", {"MatA": 60, "MatB": 40}, tolerance_g=1500)
    assert res_hi.status_code in (400, 422), res_hi.text
    assert "허용 편차" in res_hi.text


# ── (d) 수정 등록 승계: 부모 tolerance_g 물려받음 ───────────────
def test_revision_inherits_tolerance_when_unspecified():
    """수정 등록 시 tolerance_g 미지정 → 부모의 tolerance_g 승계(base_totals 와 동일 구조)."""
    client = _client()
    headers = _login(client)
    product = f"PREE{_uid()}"

    from src.db import get_connection

    # 원본을 tolerance_g=0.5 로 임포트
    base_id = _import(
        client, headers, product, {"MatA": 60, "MatB": 40}, tolerance_g=0.5
    ).json()["created_ids"][0]
    with get_connection() as conn:
        assert conn.execute(
            "SELECT tolerance_g FROM recipes WHERE id=?", (base_id,)
        ).fetchone()[0] == 0.5

    # 개정 — tolerance_g 미지정 → 승계
    rev_id = _import(
        client, headers, product, {"MatA": 70, "MatB": 30}, revision_of=base_id
    ).json()["created_ids"][0]
    with get_connection() as conn:
        rev_tol = conn.execute(
            "SELECT tolerance_g FROM recipes WHERE id=?", (rev_id,)
        ).fetchone()[0]
    assert rev_tol == 0.5


# ── (e) PUT /recipes/{id}/tolerance ────────────────────────────
def test_put_tolerance_happy_path_as_manager():
    """책임자가 PUT /recipes/{id}/tolerance 로 편차 지정 → tolerance_g 갱신."""
    client = _client()
    headers = _login(client)
    product = f"PPUT{_uid()}"
    rid = _import(client, headers, product, {"MatA": 60, "MatB": 40}).json()["created_ids"][0]

    res = client.put(
        f"/api/recipes/{rid}/tolerance",
        json={"tolerance_g": 0.8},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["tolerance_g"] == 0.8

    from src.db import get_connection

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT tolerance_g FROM recipes WHERE id=?", (rid,)
        ).fetchone()[0]
    assert stored == 0.8


def test_put_tolerance_400_when_out_of_range():
    """0 이하 또는 1000 초과 → 400(한국어 메시지)."""
    client = _client()
    headers = _login(client)
    rid = _import(
        client, headers, f"POR{_uid()}", {"MatA": 60, "MatB": 40}
    ).json()["created_ids"][0]

    res_lo = client.put(
        f"/api/recipes/{rid}/tolerance", json={"tolerance_g": 0}, headers=headers
    )
    assert res_lo.status_code == 400, res_lo.text

    res_hi = client.put(
        f"/api/recipes/{rid}/tolerance", json={"tolerance_g": 2000}, headers=headers
    )
    assert res_hi.status_code == 400, res_hi.text


def test_put_tolerance_401_403_without_manager_session():
    """비책임자(미인증) 요청 → 401/403."""
    client = _client()
    headers = _login(client)
    rid = _import(
        client, headers, f"PNM{_uid()}", {"MatA": 60, "MatB": 40}
    ).json()["created_ids"][0]

    # 헤더 없이(미인증) PUT → manager 권한 게이트에서 거부
    res = client.put(f"/api/recipes/{rid}/tolerance", json={"tolerance_g": 0.5})
    assert res.status_code in (401, 403), res.text


def test_put_tolerance_null_clears_back_to_default():
    """None 은 tolerance_g 를 해제(clear) → NULL 로 되돌아감 → 기본값 0.05."""
    client = _client()
    headers = _login(client)
    product = f"PCLR{_uid()}"
    rid = _import(client, headers, product, {"MatA": 60, "MatB": 40}).json()["created_ids"][0]

    # 먼저 값 지정
    client.put(
        f"/api/recipes/{rid}/tolerance", json={"tolerance_g": 0.8}, headers=headers
    )
    # None 으로 해제
    res = client.put(
        f"/api/recipes/{rid}/tolerance", json={"tolerance_g": None}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["tolerance_g"] is None

    from src.db import get_connection

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT tolerance_g FROM recipes WHERE id=?", (rid,)
        ).fetchone()[0]
    assert stored is None


def test_put_tolerance_404_when_recipe_missing():
    """존재하지 않는 레시피 → 404."""
    client = _client()
    headers = _login(client)
    res = client.put(
        "/api/recipes/9999999/tolerance", json={"tolerance_g": 0.5}, headers=headers
    )
    assert res.status_code == 404, res.text


# ── (f) recipe_tolerance_g: None/미확인 recipe_id 폴백 ─────────
def test_recipe_tolerance_g_falls_back_for_unknown_or_none():
    """recipe_id 가 None 이거나 존재하지 않으면 기본값 0.05."""
    conn = _make_db()
    assert bs.recipe_tolerance_g(conn, None) == bs.WEIGHING_TOLERANCE_G
    assert bs.recipe_tolerance_g(conn, 999999) == bs.WEIGHING_TOLERANCE_G


def test_recipe_tolerance_g_uses_recipe_value_when_valid():
    """유효한 tolerance_g(>0) 이면 그 값, 0 이하이면 기본값."""
    conn = _make_db()
    rid = _seed_recipe(conn)
    conn.execute("UPDATE recipes SET tolerance_g = 0.3 WHERE id = ?", (rid,))
    assert bs.recipe_tolerance_g(conn, rid) == 0.3
    # 0 이하 → 기본값 폴백
    conn.execute("UPDATE recipes SET tolerance_g = 0 WHERE id = ?", (rid,))
    assert bs.recipe_tolerance_g(conn, rid) == bs.WEIGHING_TOLERANCE_G


# ── (g) 배합 실적 저장: 레시피 편차 0.5 → 저장 / 기본 0.05 → 400 ─
def test_blend_record_uses_recipe_tolerance_for_accept_reject():
    """자재가 0.3g 편차:
    레시피 tolerance_g=0.5 → 저장(200), 기본 0.05 → 400."""
    client = _client()
    headers = _login(client)
    worker = "편차작업" + uuid.uuid4().hex[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers)

    def _record_payload(recipe_id, product):
        # 이론 100.0, 실제 100.3 → 편차 0.3g
        return {
            "recipe_id": recipe_id,
            "product_name": product,
            "worker": worker,
            "work_date": "2026-07-13",
            "total_amount": 100.0,
            "details": [
                {"material_name": "MatA", "ratio": 100,
                 "theory_amount": 100.0, "actual_amount": 100.3},
            ],
        }

    # 1) tolerance_g=0.5 인 레시피 → 0.3g 편차 저장 성공
    product_loose = f"PLOOSE{_uid()}"
    rid_loose = _import(
        client, headers, product_loose, {"MatA": 100}, tolerance_g=0.5
    ).json()["created_ids"][0]
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    saved = client.post(
        "/api/blend/records", json=_record_payload(rid_loose, product_loose), headers=headers
    )
    assert saved.status_code == 200, saved.text

    # 2) tolerance_g 미지정(기본 0.05) 레시피 → 동일 0.3g 편차 → 400
    product_strict = f"PSTRICT{_uid()}"
    rid_strict = _import(
        client, headers, product_strict, {"MatA": 100}
    ).json()["created_ids"][0]
    rejected = client.post(
        "/api/blend/records",
        json=_record_payload(rid_strict, product_strict),
        headers=headers,
    )
    assert rejected.status_code == 400, rejected.text
    # 400 detail 메시지가 '실제 적용된' 편차(0.05) 를 표시
    detail = rejected.json().get("detail")
    msg = detail if isinstance(detail, str) else str(detail)
    assert "0.05" in msg


# ── 부가: detail 응답에 tolerance_g 필드 포함 (관리 화면 표시용) ──
def test_recipe_detail_returns_tolerance_g():
    """GET /recipes/{id}/detail 은 tolerance_g 필드를 반환(anchor_material_id 와 동일 자리)."""
    client = _client()
    headers = _login(client)
    product = f"PDETAIL{_uid()}"
    rid = _import(
        client, headers, product, {"MatA": 60, "MatB": 40}, tolerance_g=0.5
    ).json()["created_ids"][0]

    detail = client.get(f"/api/recipes/{rid}/detail").json()
    assert "tolerance_g" in detail
    assert detail["tolerance_g"] == 0.5
