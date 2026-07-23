"""품목코드 자동 매칭 스크립트(tools/match_item_codes.py) 검증.

함수 단위 검증(match_materials / match_recipes / apply_confirmed). tests/test_item_code_master.py
의 픽스처 스타일(임시 DB + init_db)을 재사용한다. 커버(spec):
  - 자재: 이름 정확 매칭 → materials.code 부여.
  - 자재: 별칭 매칭(material_aliases 경유).
  - 자재: 반제품 교차 매칭(자재 'PB' 가 product 마스터 B0020 과 매칭).
  - 자재: 모호(마스터에 같은 이름 2코드) → apply 후에도 code IS NULL.
  - 레시피: product_code 가 개정 체인 전체(같은 product_name 2행)에 부여.
  - 레시피: category NULL 이면 hint 로 채움.
  - 레시피: 기존 category 와 hint 불일치 → 덮지 않음(충돌 보고).
  - 멱등: apply 2회 실행 시 결과 동일.
"""

from __future__ import annotations

import sqlite3

import src.db.connection as dbconn
from src.db import init_db
from tools.match_item_codes import (
    apply_confirmed,
    match_materials,
    match_recipes,
    _build_master_index,
)


# ---------- 헬퍼: tmp_path 에 실 init_db() 로 스키마(item_code_master 포함) ----------

def _new_conn(tmp_path) -> sqlite3.Connection:
    """tmp_path 산하 Fresh DB 로 init_db() 실행 후 연결 반환.

    get_connection() 은 모듈 전역 DATABASE_PATH/DATA_DIR 를 참조하므로 그것을 tmp_path
    로 치환하면 init_db 가 tmp_path/irms.db 에 스키마를 잡는다(다른 테스트 DB 와 격리).
    """
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "irms.db"
    dbconn.DATA_DIR = db_dir
    dbconn.DATABASE_PATH = db_path
    init_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _add_material(conn, name, *, category=None, code=None, is_active=1):
    """테스트용 자재 행 insert. id 반환."""
    cur = conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code) "
        "VALUES (?, 'weight', 'g', 'none', ?, ?, ?)",
        (name, category, is_active, code),
    )
    return cur.lastrowid


def _add_alias(conn, material_id, alias_name):
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, ?)",
        (material_id, alias_name),
    )


def _add_master(conn, code, name, *, kind, category_hint=None, source="test"):
    conn.execute(
        "INSERT INTO item_code_master (code, name, kind, category_hint, source, imported_at) "
        "VALUES (?, ?, ?, ?, ?, '2026-07-16')",
        (code, name, kind, category_hint, source),
    )


def _add_recipe(conn, product_name, *, category=None, product_code=None,
                status="completed"):
    cur = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, "
        "category, product_code) VALUES (?, ?, ?, 'tester', '2026-07-16', ?, ?)",
        (product_name, product_name, status, category, product_code),
    )
    return cur.lastrowid


def _index(conn):
    return _build_master_index(conn)


# ---------- 자재: 이름 정확 매칭 ----------

def test_material_exact_name_match_applies_code(tmp_path):
    """자재 이름이 material 마스터와 정확(정규화) 일치 → apply 시 code 부여."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "AS0001", "HEMA", kind="material")
        mid = _add_material(conn, "HEMA")  # code NULL
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["confirmed"]) == 1
        assert mat["confirmed"][0]["code"] == "AS0001"
        assert not mat["confirmed_cross"]

        apply_confirmed(conn, mat, {"confirmed": [], "category_conflict": []})
        code = conn.execute("SELECT code FROM materials WHERE id=?", (mid,)).fetchone()["code"]
        assert code == "AS0001"
    finally:
        conn.close()


# ---------- 자재: 별칭 매칭 ----------

def test_material_alias_match(tmp_path):
    """자재 이름과 달라도 material_aliases 의 별칭이 마스터와 일치하면 매칭."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "AS0099", "BYK-199", kind="material")
        mid = _add_material(conn, "분산제A")  # 본명은 마스터에 없음
        _add_alias(conn, mid, "BYK199")       # normalize → BYK199 == BYK199
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["confirmed"]) == 1
        assert mat["confirmed"][0]["code"] == "AS0099"
    finally:
        conn.close()


# ---------- 자재: 반제품 교차 매칭(PB → B0020) ----------

def test_material_cross_match_semi_finished_as_raw(tmp_path):
    """반제품을 원료로 쓰는 자재(예: PB)는 material 마스터에 없으면 product 마스터에서
    교차 매칭된다(PB → B0020). confirmed_cross 로 분류."""
    conn = _new_conn(tmp_path)
    try:
        # material 마스터에는 PB 없음. product 마스터에만 B0020 = PB.
        _add_master(conn, "B0020", "PB", kind="product", category_hint="합성")
        mid = _add_material(conn, "PB")
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["confirmed_cross"]) == 1
        assert mat["confirmed_cross"][0]["code"] == "B0020"
        assert len(mat["confirmed"]) == 0  # material 마스터엔 없었으니 1순위 0건

        apply_confirmed(conn, mat, {"confirmed": [], "category_conflict": []})
        code = conn.execute("SELECT code FROM materials WHERE id=?", (mid,)).fetchone()["code"]
        assert code == "B0020"
    finally:
        conn.close()


def test_material_prefers_material_master_over_product(tmp_path):
    """양쪽 마스터에 모두 있으면 1순위(material) 확정 — product 로 넘어가지 않는다."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "AS0001", "DUP", kind="material")
        _add_master(conn, "B0099", "DUP", kind="product")
        _add_material(conn, "DUP")
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["confirmed"]) == 1
        assert mat["confirmed"][0]["code"] == "AS0001"
        assert len(mat["confirmed_cross"]) == 0
    finally:
        conn.close()


# ---------- 자재: 모호(2코드) → apply 후에도 code IS NULL ----------

def test_material_ambiguous_stays_null_after_apply(tmp_path):
    """마스터에 같은 정규화명으로 2코드 → 모호. apply 제외 → code IS NULL 유지."""
    conn = _new_conn(tmp_path)
    try:
        # GMMA 사례: 같은 이름, 제조사 구분으로 2코드(§0). normalize 동일 → 모호.
        _add_master(conn, "AS0100", "GMMA", kind="material")
        _add_master(conn, "AS0101", "GMMA", kind="material")
        mid = _add_material(conn, "GMMA")
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["ambiguous"]) == 1
        assert set(mat["ambiguous"][0]["codes"]) == {"AS0100", "AS0101"}
        assert len(mat["confirmed"]) == 0

        apply_confirmed(conn, mat, {"confirmed": [], "category_conflict": []})
        code = conn.execute("SELECT code FROM materials WHERE id=?", (mid,)).fetchone()["code"]
        assert code is None  # 모호 → 반영 제외
    finally:
        conn.close()


def test_material_unmatched_reports_close_matches(tmp_path):
    """미매칭 시 difflib 유사 후보가 보고된다(apply 대상 아님)."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "AS0001", "CARBONBLACK", kind="material")
        _add_material(conn, "카본블랙")  # 정규화 KOREAN → CARBONBLACK 과 다름 → 미매칭
        # normalize_token('카본블랙') 은 한글이 isalnum()==True 라 보존돼 '카본블랙'(비어있지
        # 않음). 다만 영문 CARBONBLACK 과 토큰이 달라 매칭되지 않는다(빈 토큰이라서가 아님).
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["unmatched"]) == 1
        # 한글 토큰은 영문 마스터 토큰과 겹치지 않아 미매칭으로 남는다(토큰은 보존됨).
        assert mat["unmatched"][0]["name"] == "카본블랙"
    finally:
        conn.close()


# ---------- 레시피: 개정 체인 전체 product_code 부여 ----------

def test_recipe_product_code_assigned_to_full_revision_chain(tmp_path):
    """같은 product_name 의 completed 행이 2개(개정 체인) → 모두 product_code 부여."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "B0020", "PB", kind="product", category_hint="합성")
        rid1 = _add_recipe(conn, "PB")
        rid2 = _add_recipe(conn, "PB")  # 같은 이름의 다른 개정
        conn.commit()

        rec = match_recipes(conn, _index(conn))
        assert len(rec["confirmed"]) == 1
        entry = rec["confirmed"][0]
        assert entry["code"] == "B0020"
        assert set(entry["recipe_ids"]) == {rid1, rid2}

        empty_mat = {"confirmed": [], "confirmed_cross": [],
                     "ambiguous": [], "unmatched": []}
        apply_confirmed(conn, empty_mat, rec)
        # 자재는 없으니 빈 자재 구조 전달 — 레시피만 반영 검증
    finally:
        conn.close()


def test_recipe_category_filled_from_hint_when_null(tmp_path):
    """레시피 category 가 NULL → 마스터 category_hint 로 채움."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "B0020", "PB", kind="product", category_hint="합성")
        rid = _add_recipe(conn, "PB", category=None)
        conn.commit()

        rec = match_recipes(conn, _index(conn))
        empty_mat = {"confirmed": [], "confirmed_cross": [],
                     "ambiguous": [], "unmatched": []}
        apply_confirmed(conn, empty_mat, rec)

        row = conn.execute("SELECT product_code, category FROM recipes WHERE id=?",
                           (rid,)).fetchone()
        assert row["product_code"] == "B0020"
        assert row["category"] == "합성"
    finally:
        conn.close()


def test_recipe_category_conflict_not_overwritten(tmp_path):
    """기존 category 가 hint 와 다르면 덮지 않는다(충돌 보고만). product_code 는 부여."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "B0020", "PB", kind="product", category_hint="합성")
        rid = _add_recipe(conn, "PB", category="잉크")  # 기존 분류 != hint
        conn.commit()

        rec = match_recipes(conn, _index(conn))
        assert len(rec["confirmed"]) == 1
        assert len(rec["category_conflict"]) == 1
        conflict = rec["category_conflict"][0]
        assert conflict["hint"] == "합성"
        assert "잉크" in conflict["current_categories"]

        empty_mat = {"confirmed": [], "confirmed_cross": [],
                     "ambiguous": [], "unmatched": []}
        apply_confirmed(conn, empty_mat, rec)
        row = conn.execute("SELECT product_code, category FROM recipes WHERE id=?",
                           (rid,)).fetchone()
        assert row["product_code"] == "B0020"   # 코드는 부여
        assert row["category"] == "잉크"         # 분류는 덮지 않음
    finally:
        conn.close()


def test_recipe_ambiguous_and_unmatched(tmp_path):
    """레시피도 모호(2코드)·미매칭 처리는 자재와 동일."""
    conn = _new_conn(tmp_path)
    try:
        # 모호: 같은 product 이름 2코드
        _add_master(conn, "B0001", "DUP-PROD", kind="product")
        _add_master(conn, "B0002", "DUP-PROD", kind="product")
        _add_recipe(conn, "DUP-PROD")
        # 미매칭: 마스터에 없음
        _add_recipe(conn, "NO-SUCH-PRODUCT")
        conn.commit()

        rec = match_recipes(conn, _index(conn))
        assert len(rec["ambiguous"]) == 1
        assert set(rec["ambiguous"][0]["codes"]) == {"B0001", "B0002"}
        assert len(rec["unmatched"]) == 1
        assert rec["unmatched"][0]["name"] == "NO-SUCH-PRODUCT"
    finally:
        conn.close()


# ---------- 멱등성 ----------

def test_apply_idempotent_two_runs(tmp_path):
    """apply 2회 실행 결과 동일 — 이미 code/product_code 있는 행은 대상 제외."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "AS0001", "HEMA", kind="material")
        _add_master(conn, "B0020", "PB", kind="product", category_hint="합성")
        mid = _add_material(conn, "HEMA")
        rid = _add_recipe(conn, "PB", category=None)
        conn.commit()

        idx = _index(conn)
        mat1 = match_materials(conn, idx)
        rec1 = match_recipes(conn, idx)
        apply_confirmed(conn, mat1, rec1)

        # 2회차: 이미 부여된 행은 대상에서 제외되므로 confirmed 0건이 되어야 한다.
        idx2 = _index(conn)
        mat2 = match_materials(conn, idx2)
        rec2 = match_recipes(conn, idx2)
        assert len(mat2["confirmed"]) == 0  # HEMA 는 이미 code 보유 → 대상 아님
        assert len(rec2["confirmed"]) == 0  # PB 행은 이미 product_code 보유 → 대상 아님
        apply_confirmed(conn, mat2, rec2)

        # 최종 상태 불변 확인
        m_code = conn.execute("SELECT code FROM materials WHERE id=?", (mid,)).fetchone()["code"]
        r = conn.execute("SELECT product_code, category FROM recipes WHERE id=?",
                         (rid,)).fetchone()
        assert m_code == "AS0001"
        assert r["product_code"] == "B0020"
        assert r["category"] == "합성"
    finally:
        conn.close()


# ---------- 보고서: 이미 code 있는 자재는 대상 제외 ----------

def test_material_with_existing_code_excluded(tmp_path):
    """이미 code 가 부여된 자재는 매칭 대상이 아니다(자연 멱등)."""
    conn = _new_conn(tmp_path)
    try:
        _add_master(conn, "AS0001", "HEMA", kind="material")
        _add_material(conn, "HEMA", code="AS0001")  # 이미 부여
        conn.commit()

        mat = match_materials(conn, _index(conn))
        assert len(mat["confirmed"]) == 0  # 대상 제외
        assert len(mat["unmatched"]) == 0
    finally:
        conn.close()
