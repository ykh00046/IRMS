"""item-code P3 — 임포트 검증 3단 판정 + 코드 부여 + product_code 승계 검증.

설계: docs/01-plan/features/item-code.plan.md §3. spec:
scratchpad/item-code-p3-spec.md.

커버(신규 tests/test_import_item_code.py):
  1) existing: 기존 자재 → 그대로, preview material_matches 에 code 표시.
  2) master: 마스터에만 있는 이름 → 등록 후 materials.code 채워짐.
  3) unknown 차단: 마스터 비어있지 않을 때 미지 이름 → preview errors 차단, import 400.
     allow_unknown_materials=True 면 통과(코드 없이 등록).
  4) 마스터 0행 하위호환: 미지 이름이어도 기존처럼 경고만·등록됨.
  5) product_code: 마스터 매칭 시 저장 + category 자동. revision 시 부모 product_code 승계.

격리 전략: 기존 test_recipe_management/test_recipe_category 와 동일하게 공유 테스트 DB
(루트 conftest 의 IRMS_DATA_DIR)를 쓴다. 단, 이 파일의 테스트가 item_code_master 에
시드를 남기면 이어지는 테스트(원료A/원료B 미지 자재로 import)가 마스터 비어있지 않음
전제로 차단(400)되어 회귀로 이어지므로, 각 테스트는 종료 전에 시드를 비운다
(_cleanup_master). 마스터 0행 하위호환 케이스만 tmp_path 격리 DB 로 파서 단위 검증.
"""

from __future__ import annotations

import importlib
import uuid


def _client():
    """공유 테스트 DB(루트 conftest 의 IRMS_DATA_DIR)를 쓰는 TestClient 반환.

    기존 test_recipe_management/test_recipe_category 와 동일 패턴.
    """
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


def _conn():
    """현재 공유 테스트 DB 연결 반환."""
    from src.db import get_connection

    return get_connection()


def _seed_master_material(code, name):
    """item_code_master 에 material 행 INSERT(멱등 — OR IGNORE)."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO item_code_master "
            "(code, name, kind, category_hint, source, imported_at) "
            "VALUES (?, ?, 'material', NULL, 'test', '2026-07-16')",
            (code, name),
        )
        conn.commit()


def _seed_master_product(code, name, category_hint):
    """item_code_master 에 product 행 INSERT(멱등)."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO item_code_master "
            "(code, name, kind, category_hint, source, imported_at) "
            "VALUES (?, ?, 'product', ?, 'test', '2026-07-16')",
            (code, name, category_hint),
        )
        conn.commit()


def _seed_filler_materials():
    """product_code 테스트용 채움 자재(원료A/원료B)를 material 마스터에 등록.

    마스터가 비어있지 않으면 미지 자재는 차단되므로, product_code 검증에 쓰는
    채움 자재는 미리 마스터에 올려둔다(멱등)."""
    _seed_master_material("AS9991A", "원료A")
    _seed_master_material("AS9992A", "원료B")


def _cleanup_master():
    """이 테스트가 item_code_master 에 넣은 시드를 비워 공유 DB 를 원래대로(마스터 0행에
    가깝게) 복구한다. 후속 테스트가 마스터 비어있음(하위호환) 전제로 동작하도록."""
    with _conn() as conn:
        conn.execute("DELETE FROM item_code_master")
        conn.commit()


def _material_code(name):
    """자재 이름 → materials.code 조회."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT code FROM materials WHERE name = ?", (name,)
        ).fetchone()
        return row["code"] if row else None


def _recipe_row(rid):
    """레시피 id → (product_code, category) 조회."""
    with _conn() as conn:
        return conn.execute(
            "SELECT product_code, category FROM recipes WHERE id = ?", (rid,)
        ).fetchone()


# ---------- 1) existing: 기존 자재 → 그대로, material_matches 에 code 표시 ----------


def test_existing_material_shows_code_in_preview():
    """마스터가 채워진 상태에서 기존 materials 행(코드 보유) → status=existing + code 표시."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    mat_name = f"EXISTMAT{uid}"
    code = f"AS{uid}"
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code) "
                "VALUES (?, 'weight', 'g', 'none', '미분류', 1, ?)",
                (mat_name, code),
            )
            # 마스터를 비어있지 않게(하위호환 모드 해제) — 더미 1행
            conn.execute(
                "INSERT OR IGNORE INTO item_code_master "
                "(code, name, kind, category_hint, source, imported_at) "
                "VALUES ('DUMMY1', 'DUMMYMAT1', 'material', NULL, 'test', '2026-07-16')"
            )
            conn.commit()

        product = f"PEXIST{uid}"
        body = {"raw_text": f"반제품명\t{mat_name}\n{product}\t50"}
        res = client.post("/api/recipes/import/preview", json=body, headers=headers)
        assert res.status_code == 200, res.text
        matches = res.json()["material_matches"]
        target = next((m for m in matches if m["name"] == mat_name), None)
        assert target is not None
        assert target["status"] == "existing"
        assert target["code"] == code
    finally:
        _cleanup_master()


# ---------- 2) master: 마스터에만 있는 이름 → 등록 후 materials.code 채워짐 ----------


def test_master_only_material_auto_registered_with_code():
    """materials 에 없고 material 마스터 단일 히트 → 자동 등록 + materials.code 부여."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    code = f"AS{uid}"
    mat_name = f"MASTMAT{uid}"
    try:
        _seed_master_material(code, mat_name)

        product = f"PMAST{uid}"
        body = {"raw_text": f"반제품명\t{mat_name}\n{product}\t50"}
        # 미리보기 — 자재 INSERT 는 rollback 되지만 판정은 검증 가능
        res = client.post("/api/recipes/import/preview", json=body, headers=headers)
        assert res.status_code == 200, res.text
        matches = res.json()["material_matches"]
        target = next((m for m in matches if m["name"] == mat_name), None)
        assert target is not None
        assert target["status"] == "master"
        assert target["code"] == code

        # 실제 등록 → materials.code 가 마스터 코드로 채워졌는지 확인
        res2 = client.post("/api/recipes/import", json=body, headers=headers)
        assert res2.status_code == 200, res2.text
        assert _material_code(mat_name) == code
    finally:
        _cleanup_master()


def test_master_cross_match_product_master_as_material():
    """material 마스터에 없고 product 마스터에 단일 히트(반제품→원료, 예: PB→B0020)
    → status=master + 코드 부여. 교차 매칭은 여전히 master 판정."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    code = f"B{uid}"
    mat_name = f"PBMAT{uid}"
    try:
        _seed_master_product(code, mat_name, category_hint="합성")

        product = f"PCROSS{uid}"
        body = {"raw_text": f"반제품명\t{mat_name}\n{product}\t50"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        assert _material_code(mat_name) == code
    finally:
        _cleanup_master()


def test_master_ambiguous_falls_to_unknown():
    """마스터에 같은 정규화명으로 2코드 → 모호 → unknown 취급(코드 부여 안 함)."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    mat_name = f"AMBIGMAT{uid}"
    try:
        _seed_master_material(f"AS{uid}A", mat_name)
        _seed_master_material(f"AS{uid}B", mat_name)

        product = f"PAMBIG{uid}"
        body = {"raw_text": f"반제품명\t{mat_name}\n{product}\t50"}
        # 모호 → unknown → 마스터 비어있지 않으므로 기본 차단 → 400
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 400
    finally:
        _cleanup_master()


# ---------- 3) unknown 차단 + allow_unknown_materials ----------


def test_unknown_material_blocked_when_master_nonempty():
    """마스터가 비어있지 않을 때 미지 이름 → preview errors 차단 + import 400."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        # 마스터를 비어있지 않게
        _seed_master_material(f"AS{uid}", f"FILLERMAT{uid}")
        unknown_name = f"UNKNOWNMAT{uid}"  # 마스터에도 materials 에도 없음

        product = f"PUNK{uid}"
        body = {"raw_text": f"반제품명\t{unknown_name}\n{product}\t50"}
        res = client.post("/api/recipes/import/preview", json=body, headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()
        # 차단: errors 에 '마스터에 없는 품목' 항목
        assert any("마스터에 없는 품목" in e["message"] for e in data["errors"])
        target = next(
            (m for m in data["material_matches"] if m["name"] == unknown_name), None
        )
        assert target is not None
        assert target["status"] == "unknown"
        assert target["code"] is None

        # import 엔드포인트는 parsed errors 로 400
        res2 = client.post("/api/recipes/import", json=body, headers=headers)
        assert res2.status_code == 400
    finally:
        _cleanup_master()


def test_unknown_material_allowed_with_flag():
    """allow_unknown_materials=True 면 unknown 이 경고로 강등 → 통과(코드 없이 등록)."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_master_material(f"AS{uid}", f"FILLERMAT{uid}")
        unknown_name = f"UNKNOWNMAT{uid}"

        product = f"PUNKOK{uid}"
        body = {
            "raw_text": f"반제품명\t{unknown_name}\n{product}\t50",
            "allow_unknown_materials": True,
        }
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        # 코드 없이 등록
        assert _material_code(unknown_name) is None
    finally:
        _cleanup_master()


def test_unknown_material_similar_candidates_in_preview():
    """unknown 판정 시 유사 후보(similar)가 preview material_matches 에 담긴다."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        # 마스터에 CARBONBLACK{uid} 등록 — 미지명 CARBONBLAK{uid}(오타)와 유사
        master_name = f"CARBONBLACK{uid}"
        _seed_master_material(f"AS{uid}", master_name)
        typo_name = f"CARBONBLAK{uid}"

        product = f"PSIM{uid}"
        body = {"raw_text": f"반제품명\t{typo_name}\n{product}\t50"}
        res = client.post("/api/recipes/import/preview", json=body, headers=headers)
        assert res.status_code == 200, res.text
        target = next(
            (m for m in res.json()["material_matches"] if m["name"] == typo_name), None
        )
        assert target is not None
        assert target["status"] == "unknown"
        # 유사 후보가 1건 이상(정규화명이 충분히 가까우면)
        assert any(master_name in s for s in target["similar"])
    finally:
        _cleanup_master()


# ---------- 4) 마스터 0행 하위호환 ----------


def test_empty_master_backward_compat_via_parser(tmp_path):
    """마스터가 0행이면 미지 이름이어도 기존처럼 경고만 + 등록됨(차단 없음).

    이것이 기존 임포트 테스트(test_recipe_management 등)가 마스터 이관 없이도
    그대로 통과하는 핵심 하위호환 경로(spec §0). 파서 단위로 격리 DB 검증.
    """
    import sqlite3

    import src.db.connection as dbconn
    from src.db import init_db
    from src.services.import_parser import parse_import_text

    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "irms.db"
    dbconn.DATA_DIR = db_dir
    dbconn.DATABASE_PATH = db_path
    init_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 마스터 0행 확인
        assert conn.execute("SELECT COUNT(*) c FROM item_code_master").fetchone()["c"] == 0
        unknown_name = "BWCPARSERMAT"
        raw = f"반제품명\t{unknown_name}\nPROD1\t50"
        result = parse_import_text(conn, raw, allow_unknown_materials=False)
        # 하위호환: errors 에 차단 항목 없음, status=ok
        assert result["status"] == "ok", result["errors"]
        assert not any("마스터에 없는 품목" in e["message"] for e in result["errors"])
        # 경고(자동 등록)는 있음
        assert any("자동 등록" in w["message"] for w in result["warnings"])
    finally:
        conn.close()


# ---------- 5) product_code: 마스터 매칭 시 저장 + category 자동, revision 승계 ----------


def test_product_code_assigned_from_product_master():
    """반제품명이 product 마스터와 단일 히트 → recipes.product_code 저장 + category 자동 채움."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_filler_materials()
        prod_code = f"B{uid}"
        prod_name = f"PROD{uid}"
        _seed_master_product(prod_code, prod_name, category_hint="합성")

        body = {"raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t60\t40"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        rid = res.json()["created_ids"][0]
        row = _recipe_row(rid)
        assert row["product_code"] == prod_code
        assert row["category"] == "합성"  # category_hint 로 자동 채움
    finally:
        _cleanup_master()


def test_product_code_inherited_on_revision():
    """수정 등록 시 부모의 product_code 를 category 승계와 같은 자리에서 승계.

    마스터 매칭 실패(미매칭) 시에도 부모 값을 유지; 매칭 성공하면 그 값 우선.
    """
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_filler_materials()
        prod_code = f"B{uid}"
        prod_name = f"PRODREV{uid}"
        _seed_master_product(prod_code, prod_name, category_hint="합성")

        # 원본 등록 → product_code 부여됨
        body = {"raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t60\t40"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        parent_id = res.json()["created_ids"][0]
        assert _recipe_row(parent_id)["product_code"] == prod_code

        # 수정 등록(revision_of=원본) — 같은 반제품명이므로 마스터 매칭도 동일 코드
        body2 = {
            "raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t55\t45",
            "revision_of": parent_id,
        }
        res2 = client.post("/api/recipes/import", json=body2, headers=headers)
        assert res2.status_code == 200, res2.text
        child_id = res2.json()["created_ids"][0]
        assert child_id != parent_id
        child = _recipe_row(child_id)
        assert child["product_code"] == prod_code  # 승계(또는 재매칭 — 동일 값)
        assert child["category"] == "합성"  # category 도 승계
    finally:
        _cleanup_master()


def test_product_code_inherited_when_master_unmatched():
    """부모가 product_code 를 가지고 있고, 수정 등록의 반제품명이 마스터에 없어도
    부모의 product_code 를 유지하며 승계(spec §2: 마스터 매칭 실패 시에도 부모 것 유지)."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_filler_materials()
        prod_code = f"B{uid}"
        # 원본 반제품명은 마스터에 있음
        orig_name = f"PRODORIG{uid}"
        _seed_master_product(prod_code, orig_name, category_hint="합성")

        body = {"raw_text": f"반제품명\t원료A\t원료B\n{orig_name}\t60\t40"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        parent_id = res.json()["created_ids"][0]

        # 수정 등록 — 반제품명을 마스터에 없는 이름으로 바꿈. 부모 product_code 승계.
        renamed = f"PRODRENAMED{uid}"
        body2 = {
            "raw_text": f"반제품명\t원료A\t원료B\n{renamed}\t55\t45",
            "revision_of": parent_id,
        }
        res2 = client.post("/api/recipes/import", json=body2, headers=headers)
        assert res2.status_code == 200, res2.text
        child_id = res2.json()["created_ids"][0]
        child = _recipe_row(child_id)
        assert child["product_code"] == prod_code  # 부모 값 승계
    finally:
        _cleanup_master()


def test_product_code_null_when_product_unmatched():
    """신규(비개정) 임포트에서 반제품명이 product 마스터와 매칭 실패 → product_code NULL."""
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_filler_materials()
        prod_name = f"PRODNOCODE{uid}"  # product 마스터에 없음

        body = {"raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t60\t40"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        rid = res.json()["created_ids"][0]
        row = _recipe_row(rid)
        assert row["product_code"] is None
    finally:
        _cleanup_master()


def test_existing_category_not_overwritten_by_hint():
    """마스터 category_hint 가 있어도 부모(또는 기존) category 가 있으면 덮지 않는다.

    match_item_codes.py(P2) 규칙과 동일 — 비어있을 때만 채운다. 수정 등록에서는
    inherited_category(부모 값)가 hint 보다 우선한다.
    """
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_filler_materials()
        prod_code = f"B{uid}"
        prod_name = f"PRODCAT{uid}"
        # 마스터 hint = 합성
        _seed_master_product(prod_code, prod_name, category_hint="합성")

        # 원본 등록 → category 가 hint(합성) 로 자동 채워짐
        body = {"raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t60\t40"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        parent_id = res.json()["created_ids"][0]
        assert _recipe_row(parent_id)["category"] == "합성"

        # 부모 category 를 약품으로 수동 변경
        res_cat = client.put(
            f"/api/recipes/{parent_id}/category", json={"category": "약품"}, headers=headers
        )
        assert res_cat.status_code == 200

        # 수정 등록 — inherited_category(약품)가 hint(합성)보다 우선 → 약품 유지
        body2 = {
            "raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t55\t45",
            "revision_of": parent_id,
        }
        res2 = client.post("/api/recipes/import", json=body2, headers=headers)
        assert res2.status_code == 200, res2.text
        child_id = res2.json()["created_ids"][0]
        child = _recipe_row(child_id)
        assert child["category"] == "약품"  # hint(합성) 로 덮이지 않음
        assert child["product_code"] == prod_code
    finally:
        _cleanup_master()


# ---------- P6: 목록 API 응답에 product_code 노출 ----------

def test_list_recipes_and_blend_recipes_expose_product_code():
    """GET /api/recipes (recipe_operator_routes.list_recipes) 와
    GET /api/blend/recipes (blend_service.list_blend_recipes) 응답 항목에 product_code
    필드가 있다. 값은 P3 등록 경로(반제품명 ↔ product 마스터 단일 히트 → 자동 부여).

    UI 변경 없이 응답 필드만 노출하는 P6 범위.
    """
    client = _client()
    headers = _login(client)
    uid = _uid()
    try:
        _seed_filler_materials()
        prod_code = f"B{uid}"
        prod_name = f"PRODLIST{uid}"
        _seed_master_product(prod_code, prod_name, category_hint="합성")

        body = {"raw_text": f"반제품명\t원료A\t원료B\n{prod_name}\t60\t40"}
        res = client.post("/api/recipes/import", json=body, headers=headers)
        assert res.status_code == 200, res.text
        rid = res.json()["created_ids"][0]
        assert _recipe_row(rid)["product_code"] == prod_code  # 부여 전제

        # 1) 레시피 현황 목록 — product_code 필드 존재 + 값 일치
        items = client.get("/api/recipes").json()["items"]
        target = next(it for it in items if it["id"] == rid)
        assert "product_code" in target
        assert target["product_code"] == prod_code

        # 2) 배합 선택 목록 — product_code 필드 존재 + 값 일치
        blend_items = client.get("/api/blend/recipes").json()["items"]
        blend_target = next(it for it in blend_items if it["id"] == rid)
        assert "product_code" in blend_target
        assert blend_target["product_code"] == prod_code
    finally:
        _cleanup_master()
