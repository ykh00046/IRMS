"""reactor-ownership — 반응기 사용 여부(use_reactor) 소유를 viscosity_products → recipes 로 이전.

검증 범위:
 1. 마이그레이션(recipes_use_reactor_from_viscosity) — 점도 제품 use_reactor=1 매칭 레시피 시드.
 2. blend_service.product_uses_reactor() — 최신 completed 레시피 값 우선, 없으면 점도 폴백.
 3. 수정 등록(개정) 시 부모 use_reactor 승계(tolerance_g/category 와 동일 구조).
 4. 명시적 ImportRequest.use_reactor 가 승계·기본값보다 우선.
 5. PUT /recipes/{id}/use-reactor — 책임자 전용(auth) + 토글 + 관리 목록 반영.

기존 test_recipe_category.py 의 실제 패턴(client/management-login/csrf/import)을 따른다.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.db import init_db
from src.db import migrations as migrations_mod
from src.services import blend_service as bs


# ── 마이그레이션 시드: init_db() 로 실 스키마를 잡고 마이그레이션 동작 확인 ──

def _fresh_conn(tmp_path) -> sqlite3.Connection:
    """tmp_path 산하 Fresh DB 로 init_db() 실행 후 연결 반환(test_item_code_master 패턴).

    get_connection() 은 src.db.connection 의 모듈 전역 DATABASE_PATH/DATA_DIR 를 쓰므로
    그 네임스페이스를 직접 치환해야 init_db 가 tmp_path/irms.db 에 스키마를 잡는다.
    """
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


def test_init_db_adds_column_and_records_migration(tmp_path):
    """init_db() 가 새 컬럼(recipes.use_reactor)을 잡고 마이그레이션을 기록한다."""
    conn = _fresh_conn(tmp_path)
    assert migrations_mod.has_migration(conn, "recipes_use_reactor_from_viscosity")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()}
    assert "use_reactor" in cols


def test_migration_seeds_use_reactor_from_viscosity(tmp_path):
    """recipes_use_reactor_from_viscosity seed UPDATE — 점도 use_reactor=1 제품(name 또는 code
    일치)의 레시피를 1 로 시드. 매칭 안 된 레시피는 0 유지.

    init_db() 가 이미 마이그레이션을 1회 실행했으므로, seed UPDATE 가 내 테스트 데이터에
    동작하는지 보려면 마이그레이션 기록을 지우고 내 데이터를 넣은 뒤 다시 실행해야 한다.
    컬럼은 이미 존재하므로 ensure_column 은 no-op 이고, seed UPDATE 만 재실행된다.
    """
    conn = _fresh_conn(tmp_path)
    # 점도 제품(use_reactor=1) — name 매칭 + code 매칭 대상.
    conn.execute(
        "INSERT INTO viscosity_products (code, name, use_reactor, is_active, created_at) "
        "VALUES (?, ?, 1, 1, '2026-01-01T00:00:00Z')",
        ("RX01", "반응기제품A"),
    )
    conn.execute(
        "INSERT INTO viscosity_products (code, name, use_reactor, is_active, created_at) "
        "VALUES (?, ?, 1, 1, '2026-01-01T00:00:00Z')",
        ("RX02", "코드매칭용"),
    )
    # use_reactor=0 점도 제품 — 매칭되어도 레시피는 0 이어야 함.
    conn.execute(
        "INSERT INTO viscosity_products (code, name, use_reactor, is_active, created_at) "
        "VALUES (?, ?, 0, 1, '2026-01-01T00:00:00Z')",
        ("NR00", "비반응기B"),
    )
    # 레시피 시드: name 매칭 / code 매칭(RX02) / 비반응기 / 완전 무관.
    for product in ("반응기제품A", "RX02", "비반응기B", "완전무관C"):
        conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, completed_at) "
            "VALUES (?, ?, 'completed', 't', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (product, product),
        )
    # 마이그레이션 기록을 지워 seed UPDATE 가 재실행되게 한다(컬럼은 이미 있음 → no-op).
    conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'recipes_use_reactor_from_viscosity'"
    )
    conn.commit()
    migrations_mod.apply_schema_migrations(conn)

    def use(product):
        return int(conn.execute(
            "SELECT use_reactor FROM recipes WHERE product_name = ?", (product,)
        ).fetchone()["use_reactor"])

    assert use("반응기제품A") == 1   # name 매칭
    assert use("RX02") == 1          # code 매칭
    assert use("비반응기B") == 0      # 점도 use_reactor=0 → 시드 안 됨
    assert use("완전무관C") == 0      # 점도 매칭 없음 → 0(기본값)
    # 마이그레이션은 재기록되어야 함.
    assert migrations_mod.has_migration(conn, "recipes_use_reactor_from_viscosity")


def test_migration_seed_idempotent(tmp_path):
    """마이그레이션 재실행 시 seed UPDATE 가 skip — 사용자가 뒤집은 값을 덮어쓰지 않는다."""
    conn = _fresh_conn(tmp_path)
    conn.execute(
        "INSERT INTO viscosity_products (code, name, use_reactor, is_active, created_at) "
        "VALUES ('RX9', '시드대상', 1, 1, '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, completed_at) "
        "VALUES ('시드대상', '시드대상', 'completed', 't', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "DELETE FROM schema_migrations WHERE name = 'recipes_use_reactor_from_viscosity'"
    )
    conn.commit()
    migrations_mod.apply_schema_migrations(conn)  # 1차 시드 → 1
    assert int(conn.execute(
        "SELECT use_reactor FROM recipes WHERE product_name='시드대상'"
    ).fetchone()["use_reactor"]) == 1
    # 사용자가 PUT 으로 0 으로 뒤집은 뒤 재실행 → 기록되어 있어 seed skip, 값 유지.
    conn.execute("UPDATE recipes SET use_reactor=0 WHERE product_name='시드대상'")
    conn.commit()
    migrations_mod.apply_schema_migrations(conn)
    assert int(conn.execute(
        "SELECT use_reactor FROM recipes WHERE product_name='시드대상'"
    ).fetchone()["use_reactor"]) == 0


# ── product_uses_reactor: 레시피 우선 + 점도 폴백 ──

def _mem_conn() -> sqlite3.Connection:
    """단위테스트용 최소 스키마(recipes.use_reactor 포함)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, ink_name TEXT, status TEXT DEFAULT 'completed',
            use_reactor INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT, name TEXT,
            use_reactor INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def test_product_uses_reactor_reads_latest_recipe():
    """최신 completed 레시피(recipes.use_reactor) 값을 따른다(ORDER BY id DESC LIMIT 1)."""
    conn = _mem_conn()
    # 같은 제품의 옛 버전은 1, 최신 버전은 0 → 최신(0)이 승리.
    conn.execute(
        "INSERT INTO recipes (product_name, status, use_reactor, created_at) "
        "VALUES ('P1', 'completed', 1, '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO recipes (product_name, status, use_reactor, created_at) "
        "VALUES ('P1', 'completed', 0, '2026-02-01')"
    )
    conn.commit()
    assert bs.product_uses_reactor(conn, "P1") is False


def test_product_uses_reactor_falls_back_to_viscosity_when_no_recipe():
    """매칭 레시피 없으면 점도 설정(viscosity_products.use_reactor)으로 폴백(레거시 호환)."""
    conn = _mem_conn()
    conn.execute(
        "INSERT INTO viscosity_products (code, name, use_reactor) VALUES ('VL', '레거시P', 1)"
    )
    conn.commit()
    # 레시피 없음 → 점도값(1) 사용.
    assert bs.product_uses_reactor(conn, "레거시P") is True
    assert bs.product_uses_reactor(conn, "VL") is True


def test_product_uses_reactor_empty_name():
    """빈 제품명 → False."""
    assert bs.product_uses_reactor(_mem_conn(), "") is False
    assert bs.product_uses_reactor(_mem_conn(), "   ") is False


# ── API: client/login/import 헬퍼(test_recipe_category 패턴) ──

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
    """레시피 1건 등록(등록 즉시 completed) → id 반환. extra 로 use_reactor/revision_of 주입."""
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    body.update(extra)
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


# ── 수정 등록 승계 + 명시적 우선 ──

def test_use_reactor_inherited_on_revision():
    """수정 등록(개정) 시 부모의 use_reactor 승계 — tolerance_g/category 와 동일 구조.
    요청에 use_reactor 가 없으면 부모 값을 그대로 물려받는다.
    """
    client = _client()
    headers = _login(client)
    product = f"RUR{_uid()}"
    rid = _import(client, headers, product)
    # 부모를 use_reactor=1 로 설정(PUT).
    res = client.put(f"/api/recipes/{rid}/use-reactor", json={"use_reactor": True}, headers=headers)
    assert res.status_code == 200, res.text

    # 수정 등록 — use_reactor 미지정 → 부모(1) 승계.
    new_id = _import(client, headers, product, revision_of=rid)
    assert new_id != rid
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == new_id)
    assert mine["use_reactor"] == 1


def test_explicit_use_reactor_wins_over_inheritance():
    """명시적 ImportRequest.use_reactor=True 가 부모(0) 승계보다 우선한다."""
    client = _client()
    headers = _login(client)
    product = f"EUR{_uid()}"
    rid = _import(client, headers, product)  # 부모 기본 0
    # 수정 등록에서 명시적으로 use_reactor=True 지정.
    new_id = _import(client, headers, product, revision_of=rid, use_reactor=True)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == new_id)
    assert mine["use_reactor"] == 1


def test_new_recipe_defaults_use_reactor_zero():
    """비개정 신규 레시피는 use_reactor=0 으로 시작(기본값)."""
    client = _client()
    headers = _login(client)
    product = f"NRR{_uid()}"
    rid = _import(client, headers, product)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == rid)
    assert mine["use_reactor"] == 0


# ── PUT /recipes/{id}/use-reactor: auth + 토글 + 반영 ──

def test_manager_toggles_use_reactor_reflected_in_payloads():
    """책임자가 use_reactor=true → 200, 관리 목록·상세·이력 모두에 반영."""
    client = _client()
    headers = _login(client)
    product = f"TUR{_uid()}"
    rid = _import(client, headers, product)

    res = client.put(f"/api/recipes/{rid}/use-reactor", json={"use_reactor": True}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["use_reactor"] is True

    # 관리 목록(GET /api/recipes)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    assert next(it for it in items if it["id"] == rid)["use_reactor"] == 1

    # 상세(GET /api/recipes/{id}/detail)
    detail = client.get(f"/api/recipes/{rid}/detail").json()
    assert detail["use_reactor"] == 1

    # 토글 → false 로 되돌림.
    res2 = client.put(f"/api/recipes/{rid}/use-reactor", json={"use_reactor": False}, headers=headers)
    assert res2.status_code == 200, res2.text
    assert res2.json()["use_reactor"] is False
    detail2 = client.get(f"/api/recipes/{rid}/detail").json()
    assert detail2["use_reactor"] == 0


def test_use_reactor_in_history():
    """버전 이력(GET /api/recipes/{id}/history) 항목에 use_reactor 가 노출된다."""
    client = _client()
    headers = _login(client)
    product = f"HUR{_uid()}"
    rid = _import(client, headers, product)
    client.put(f"/api/recipes/{rid}/use-reactor", json={"use_reactor": True}, headers=headers)
    new_id = _import(client, headers, product, revision_of=rid)  # 승계 → 1

    hist = client.get(f"/api/recipes/{new_id}/history").json()["items"]
    by_id = {it["id"]: it for it in hist}
    assert by_id[rid]["use_reactor"] is True
    assert by_id[new_id]["use_reactor"] is True


def test_non_manager_blocked_from_use_reactor():
    """미로그인(또는 비책임자) → 401 또는 403. category/tolerance 엔드포인트와 동일 기대."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"BUR{_uid()}")  # 시드만 책임자로

    blocked = client.put(f"/api/recipes/{rid}/use-reactor", json={"use_reactor": True})
    assert blocked.status_code in (401, 403)


def test_use_reactor_invalid_body_rejected():
    """use_reactor 가 bool 이 아니거나 누락 → 400."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"IUR{_uid()}")
    # 문자열
    assert client.put(
        f"/api/recipes/{rid}/use-reactor", json={"use_reactor": "yes"}, headers=headers
    ).status_code == 400
    # 누락
    assert client.put(
        f"/api/recipes/{rid}/use-reactor", json={}, headers=headers
    ).status_code == 400


def test_use_reactor_unknown_recipe_404():
    """존재하지 않는 레시피 id → 404."""
    client = _client()
    headers = _login(client)
    res = client.put("/api/recipes/999999/use-reactor", json={"use_reactor": True}, headers=headers)
    assert res.status_code == 404
