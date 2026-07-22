"""1차→2차 레시피 연계(recipes.stage1_recipe_id) + 자체 제조 반제품 자재 인식.

검증 범위:
 1. 마이그레이션(recipes_stage1_recipe_id) — 컬럼 추가, 기본 NULL, 시드 없음.
 2. import_parser 자체 제조 반제품 인식 — 마스터에 없어도 completed 레시피 product_name 과
    일치하는 자재는 status="recipe" 로 정상 인식(unknown 차단 우회).
 3. ImportRequest.stage1_recipe_id 명시 값 저장; 수정 등록(개정) 시 None 이면 부모 승계.
 4. PUT /recipes/{id}/stage1 — set(반영), clear(null), 404 unknown, 400 self-reference,
    non-manager blocked.

test_recipe_use_reactor.py / test_recipe_derived.py 의 client/management-login/csrf/import 패턴.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.db import init_db
from src.db import migrations as migrations_mod
from src.services import import_parser


# ── 마이그레이션: init_db() 로 실 스키마를 잡고 컬럼 추가 확인 ──

def _fresh_conn(tmp_path) -> sqlite3.Connection:
    """tmp_path 산하 Fresh DB 로 init_db() 실행 후 연결 반환(test_item_code_master 패턴)."""
    import src.db.connection as dbconn
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "irms.db"
    dbconn.DATA_DIR = db_dir
    dbconn.DATABASE_PATH = db_path
    init_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_init_db_adds_stage1_recipe_id_column(tmp_path):
    """init_db() 가 새 컬럼(recipes.stage1_recipe_id)을 잡고 마이그레이션을 기록한다."""
    conn = _fresh_conn(tmp_path)
    assert migrations_mod.has_migration(conn, "recipes_stage1_recipe_id")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()}
    assert "stage1_recipe_id" in cols


def test_stage1_recipe_id_defaults_null(tmp_path):
    """stage1_recipe_id 컬럼은 기본 NULL — 데이터 시드 없음."""
    conn = _fresh_conn(tmp_path)
    conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, completed_at) "
        "VALUES ('연계테스트', '연계테스트', 'completed', 't', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT stage1_recipe_id FROM recipes WHERE product_name = '연계테스트'"
    ).fetchone()
    assert row["stage1_recipe_id"] is None  # 시드 없음 — 기본 NULL


# ── import_parser: 자체 제조 반제품(1차) 자재 인식 ──

def _parser_conn() -> sqlite3.Connection:
    """import_parser 단위테스트용 최소 스키마(test_import_parser._make_db 패턴).
    materials + material_aliases + recipes + item_code_master(빈) — 마스터가 비어있지 않아야
    unknown 차단 로직이 동작하므로 item_code_master 를 빈 행으로 둔다.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit_type TEXT NOT NULL DEFAULT 'weight',
            unit TEXT NOT NULL DEFAULT 'g',
            color_group TEXT NOT NULL DEFAULT 'none',
            category TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            code TEXT
        );
        CREATE TABLE material_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            alias_name TEXT NOT NULL
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER,
            material_id INTEGER NOT NULL
        );
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, status TEXT DEFAULT 'completed', created_at TEXT
        );
        CREATE TABLE item_code_master (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            category_hint TEXT,
            is_active INTEGER DEFAULT 1
        );
        """
    )
    return conn


def test_parser_recognizes_completed_recipe_product_as_material():
    """completed 레시피의 product_name 과 일치하는 자재는 status="recipe" 로 정상 인식.

    마스터에 없어도 unknown 차단 없이 자동 등록(1차 반제품 연계).
    """
    conn = _parser_conn()
    # 1차 반제품 레시피(completed) 등록.
    conn.execute(
        "INSERT INTO recipes (product_name, status) VALUES ('SBCT-1', 'completed')"
    )
    conn.commit()
    # 2차 레시피 임포트 — 자재 중 'SBCT-1' 은 마스터에 없지만 completed 레시피 product_name.
    result = import_parser.parse_import_text(
        conn,
        "반제품명\tSBCT-1\t원료A\n2차제품\t50\t50",
    )
    # errors 가 비어야 한다(unknown 차단 없음).
    assert result["errors"] == [], result["errors"]
    matches = {m["name"]: m for m in result["material_matches"]}
    assert matches["SBCT-1"]["status"] == "recipe"
    assert matches["SBCT-1"]["code"] is None
    # '원료A' 도 마스터에 없고 레시피 product_name 도 아니면 unknown(차단).
    assert matches["원료A"]["status"] == "unknown"


def test_parser_recipe_match_does_not_add_errors():
    """자체 제조 반제품(1차) 인식은 마스터 유무와 무관하게 errors 에 추가하지 않는다."""
    conn = _parser_conn()
    conn.execute(
        "INSERT INTO recipes (product_name, status) VALUES ('1차반제품', 'completed')"
    )
    conn.commit()
    result = import_parser.parse_import_text(
        conn,
        "반제품명\t1차반제품\n2차\t100",
    )
    assert result["errors"] == []
    matches = {m["name"]: m for m in result["material_matches"]}
    assert matches["1차반제품"]["status"] == "recipe"


def test_parser_non_completed_recipe_not_recognized():
    """status != completed 인 레시피 product_name 은 인식 대상 아님(canceled 등)."""
    conn = _parser_conn()
    conn.execute(
        "INSERT INTO recipes (product_name, status) VALUES ('취소된1차', 'canceled')"
    )
    conn.commit()
    result = import_parser.parse_import_text(
        conn,
        "반제품명\t취소된1차\n2차\t100",
    )
    # canceled 레시피는 인식 대상 아니므로 unknown 차단.
    matches = {m["name"]: m for m in result["material_matches"]}
    assert matches["취소된1차"]["status"] == "unknown"


# ── API: client/login/import 헬퍼 ──

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


def _import(client, headers, product, a=60, b=40, **extra):
    """레시피 1건 등록(등록 즉시 completed) → id 반환. extra 로 stage1_recipe_id/revision_of 주입."""
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    body.update(extra)
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


# ── ImportRequest.stage1_recipe_id: 명시 값 + 승계 ──

def test_explicit_stage1_recipe_id_stored():
    """명시적 ImportRequest.stage1_recipe_id 가 저장된다(관리 목록에 반영)."""
    client = _client()
    headers = _login(client)
    # 1차 레시피 먼저 등록.
    stage1_id = _import(client, headers, f"S1_{_uid()}")
    # 2차 레시피 등록 — stage1_recipe_id 명시.
    stage2_id = _import(client, headers, f"S2_{_uid()}", stage1_recipe_id=stage1_id)
    detail = client.get(f"/api/recipes/{stage2_id}/detail").json()
    assert detail["stage1_recipe_id"] == stage1_id


def test_stage1_recipe_id_inherited_on_revision():
    """수정 등록(개정) 시 부모의 stage1_recipe_id 승계(use_reactor/is_derived 와 동일 구조).
    요청에 stage1_recipe_id 가 없으면 부모 값을 물려받는다.
    """
    client = _client()
    headers = _login(client)
    stage1_id = _import(client, headers, f"P1_{_uid()}")
    # 부모 2차 레시피에 stage1 링크 설정(PUT).
    parent2 = _import(client, headers, f"P2_{_uid()}")
    res = client.put(f"/api/recipes/{parent2}/stage1",
                     json={"stage1_recipe_id": stage1_id}, headers=headers)
    assert res.status_code == 200, res.text
    # 수정 등록 — stage1_recipe_id 미지정 → 부모 값 승계.
    child = _import(client, headers, f"P2_{_uid()}", revision_of=parent2)
    detail = client.get(f"/api/recipes/{child}/detail").json()
    assert detail["stage1_recipe_id"] == stage1_id


def test_new_recipe_defaults_stage1_null():
    """비개정 신규 레시피는 stage1_recipe_id = NULL(기본값)."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"NR_{_uid()}")
    detail = client.get(f"/api/recipes/{rid}/detail").json()
    assert detail["stage1_recipe_id"] is None


# ── PUT /recipes/{id}/stage1: set / clear / validation ──

def test_manager_sets_stage1_reflected_in_payloads():
    """책임자가 stage1 설정 → 200, 관리 목록·상세에 stage1_recipe_id + stage1_product_name 반영."""
    client = _client()
    headers = _login(client)
    stage1_id = _import(client, headers, f"TS1_{_uid()}")
    stage2_id = _import(client, headers, f"TS2_{_uid()}")

    res = client.put(f"/api/recipes/{stage2_id}/stage1",
                     json={"stage1_recipe_id": stage1_id}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["stage1_recipe_id"] == stage1_id

    # 상세 — stage1_recipe_id + stage1_product_name.
    detail = client.get(f"/api/recipes/{stage2_id}/detail").json()
    assert detail["stage1_recipe_id"] == stage1_id
    stage1_name = client.get(f"/api/recipes/{stage1_id}/detail").json()["product_name"]
    assert detail["stage1_product_name"] == stage1_name

    # 관리 목록에도 반영.
    items = client.get("/api/recipes").json()["items"]
    mine = next(it for it in items if it["id"] == stage2_id)
    assert mine["stage1_recipe_id"] == stage1_id
    assert mine["stage1_product_name"] == stage1_name


def test_manager_clears_stage1_to_null():
    """PUT stage1_recipe_id=null → 링크 해제(stage1_recipe_id=None)."""
    client = _client()
    headers = _login(client)
    stage1_id = _import(client, headers, f"CS1_{_uid()}")
    stage2_id = _import(client, headers, f"CS2_{_uid()}")
    client.put(f"/api/recipes/{stage2_id}/stage1",
               json={"stage1_recipe_id": stage1_id}, headers=headers)
    # 해제.
    res = client.put(f"/api/recipes/{stage2_id}/stage1",
                     json={"stage1_recipe_id": None}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["stage1_recipe_id"] is None
    detail = client.get(f"/api/recipes/{stage2_id}/detail").json()
    assert detail["stage1_recipe_id"] is None
    assert detail["stage1_product_name"] is None


def test_stage1_self_reference_rejected():
    """stage1_recipe_id == recipe_id → 400(자기 자신을 1차로 지정 불가)."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"SR_{_uid()}")
    res = client.put(f"/api/recipes/{rid}/stage1",
                     json={"stage1_recipe_id": rid}, headers=headers)
    assert res.status_code == 400
    assert "자기 자신" in res.json()["detail"]


def test_stage1_unknown_recipe_404():
    """존재하지 않는 recipe_id → 404."""
    client = _client()
    headers = _login(client)
    res = client.put("/api/recipes/999999/stage1",
                     json={"stage1_recipe_id": 1}, headers=headers)
    assert res.status_code == 404


def test_stage1_nonexistent_target_rejected():
    """stage1_recipe_id 가 존재하지 않는 레시피 → 400(대상 1차 레시피를 찾을 수 없음)."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"NT_{_uid()}")
    res = client.put(f"/api/recipes/{rid}/stage1",
                     json={"stage1_recipe_id": 999999}, headers=headers)
    assert res.status_code == 400


def test_stage1_invalid_body_rejected():
    """stage1_recipe_id 가 정수/null 이 아니면 → 400."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"IB_{_uid()}")
    # 문자열(숫자 아님).
    res = client.put(f"/api/recipes/{rid}/stage1",
                     json={"stage1_recipe_id": "abc"}, headers=headers)
    assert res.status_code == 400


def test_non_manager_blocked_from_stage1():
    """미로그인(또는 비책임자) → 401 또는 403."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"BM_{_uid()}")  # 시드만 책임자로
    blocked = client.put(f"/api/recipes/{rid}/stage1", json={"stage1_recipe_id": rid})
    assert blocked.status_code in (401, 403)


def test_stage1_direct_cycle_rejected():
    """(f) GAP 4: A.stage1=B 설정 후 B.stage1=A 시도 → 400(2노드 순환 차단, 유한 걸음)."""
    client = _client()
    headers = _login(client)
    a = _import(client, headers, f"CYCA_{_uid()}")
    b = _import(client, headers, f"CYCB_{_uid()}")

    r1 = client.put(f"/api/recipes/{a}/stage1", json={"stage1_recipe_id": b}, headers=headers)
    assert r1.status_code == 200, r1.text
    # B → A 는 순환을 만든다(B→A→B).
    r2 = client.put(f"/api/recipes/{b}/stage1", json={"stage1_recipe_id": a}, headers=headers)
    assert r2.status_code == 400, r2.text
    assert "순환" in r2.json()["detail"]


def test_stage1_reference_cleared_on_delete():
    """(g) GAP 4: 1차 레시피 삭제 시 그것을 stage1 로 참조하던 2차의 링크가 NULL 로 정리."""
    client = _client()
    headers = _login(client)
    stage1_id = _import(client, headers, f"DELS1_{_uid()}")
    stage2_id = _import(client, headers, f"DELS2_{_uid()}")
    client.put(
        f"/api/recipes/{stage2_id}/stage1",
        json={"stage1_recipe_id": stage1_id},
        headers=headers,
    )

    res = client.delete(f"/api/recipes/{stage1_id}", headers=headers)
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/recipes/{stage2_id}/detail").json()
    assert detail["stage1_recipe_id"] is None  # 댕글링 정리됨


def test_registration_dangling_stage1_rejected():
    """GAP 4: 존재하지 않는 stage1_recipe_id 를 명시한 등록 → 400."""
    client = _client()
    headers = _login(client)
    res = client.post(
        "/api/recipes/import",
        json={
            "raw_text": f"반제품명\t원료A\t원료B\nDANGS2_{_uid()}\t60\t40",
            "stage1_recipe_id": 999999,
        },
        headers=headers,
    )
    assert res.status_code == 400, res.text
    assert "1차 레시피" in res.json()["detail"]


def test_stage1_in_history():
    """버전 이력(GET /api/recipes/{id}/history) 항목에 stage1_recipe_id 가 노출된다."""
    client = _client()
    headers = _login(client)
    stage1_id = _import(client, headers, f"HS1_{_uid()}")
    parent = _import(client, headers, f"HS2_{_uid()}")
    client.put(f"/api/recipes/{parent}/stage1",
               json={"stage1_recipe_id": stage1_id}, headers=headers)
    child = _import(client, headers, f"HS2_{_uid()}", revision_of=parent)  # 승계

    hist = client.get(f"/api/recipes/{child}/history").json()["items"]
    by_id = {it["id"]: it for it in hist}
    assert by_id[parent]["stage1_recipe_id"] == stage1_id
    assert by_id[child]["stage1_recipe_id"] == stage1_id  # 승계
