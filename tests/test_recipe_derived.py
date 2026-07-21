"""파생(derived) — recipes.is_derived 소유. use_reactor 와 **독립**적인 레시피 속성.

검증 범위:
 1. 마이그레이션(recipes_is_derived) — 컬럼 추가, 기본값 0, 데이터 시드 **없음**.
    (use_reactor 와 달리 점도 매칭 시드가 없다 — 사용자가 레시피마다 지정.)
 2. blend_service.recipe_is_derived() — recipe_id 의 is_derived 값 읽기(None/컬럼없음 → False).
 3. 수정 등록(개정) 시 부모 is_derived 승계(use_reactor/tolerance_g 와 동일 구조).
 4. 명시적 ImportRequest.is_derived 가 승계·기본값보다 우선.
 5. PUT /recipes/{id}/derived — 책임자 전용(auth) + 토글 + 관리 목록·상세·이력 반영.

test_recipe_use_reactor.py 의 실제 패턴(client/management-login/csrf/import)을 따른다.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.db import init_db
from src.db import migrations as migrations_mod
from src.services import blend_service as bs


# ── 마이그레이션: init_db() 로 실 스키마를 잡고 컬럼 추가 확인 ──

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


def test_init_db_adds_is_derived_column_and_records_migration(tmp_path):
    """init_db() 가 새 컬럼(recipes.is_derived)을 잡고 마이그레이션을 기록한다."""
    conn = _fresh_conn(tmp_path)
    assert migrations_mod.has_migration(conn, "recipes_is_derived")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(recipes)").fetchall()}
    assert "is_derived" in cols


def test_is_derived_defaults_zero_no_seeding(tmp_path):
    """is_derived 컬럼은 기본값 0 — use_reactor 시드와 달리 데이터 시드가 없다.

    기존 레시피는 모두 is_derived=0 으로 둔다(사용자가 레시피마다 지정).
    """
    conn = _fresh_conn(tmp_path)
    # 레시피 1건 시드 후 is_derived 확인.
    conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, completed_at) "
        "VALUES ('파생테스트', '파생테스트', 'completed', 't', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT is_derived FROM recipes WHERE product_name = '파생테스트'"
    ).fetchone()
    assert int(row["is_derived"]) == 0  # 시드 없음 — 기본값 0


# ── recipe_is_derived: recipe_id 기반 읽기 ──

def _mem_conn() -> sqlite3.Connection:
    """단위테스트용 최소 스키마(recipes.is_derived 포함)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT, ink_name TEXT, status TEXT DEFAULT 'completed',
            is_derived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        );
        """
    )
    return conn


def test_recipe_is_derived_reads_value():
    """recipe_is_derived() — recipe_id 의 is_derived 값을 그대로 반환."""
    conn = _mem_conn()
    cur = conn.execute(
        "INSERT INTO recipes (product_name, status, is_derived) VALUES ('P1', 'completed', 1)"
    )
    rid = cur.lastrowid
    conn.commit()
    assert bs.recipe_is_derived(conn, rid) is True


def test_recipe_is_derived_false_for_zero():
    """is_derived=0 → False."""
    conn = _mem_conn()
    cur = conn.execute(
        "INSERT INTO recipes (product_name, status, is_derived) VALUES ('P2', 'completed', 0)"
    )
    rid = cur.lastrowid
    conn.commit()
    assert bs.recipe_is_derived(conn, rid) is False


def test_recipe_is_derived_none_recipe_id():
    """recipe_id=None → False(레시피 없는 경로)."""
    assert bs.recipe_is_derived(_mem_conn(), None) is False


def test_recipe_is_derived_missing_column_returns_false():
    """is_derived 컬럼이 없는 구버전/테스트 스키마 → OperationalError 폴백으로 False."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE recipes (id INTEGER PRIMARY KEY AUTOINCREMENT, product_name TEXT);"
    )
    conn.execute("INSERT INTO recipes (product_name) VALUES ('P3')")
    conn.commit()
    # 컬럼이 없어도 예외 없이 False (anchor_material_id 폴백과 동일 방어).
    assert bs.recipe_is_derived(conn, 1) is False


# ── API: client/login/import 헬퍼(test_recipe_use_reactor 패턴) ──

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
    """레시피 1건 등록(등록 즉시 completed) → id 반환. extra 로 is_derived/revision_of 주입."""
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    body.update(extra)
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


# ── 수정 등록 승계 + 명시적 우선 ──

def test_is_derived_inherited_on_revision():
    """수정 등록(개정) 시 부모의 is_derived 승계 — use_reactor/tolerance_g 와 동일 구조.
    요청에 is_derived 가 없으면 부모 값을 그대로 물려받는다.
    """
    client = _client()
    headers = _login(client)
    product = f"RID{_uid()}"
    rid = _import(client, headers, product)
    # 부모를 is_derived=1 로 설정(PUT).
    res = client.put(f"/api/recipes/{rid}/derived", json={"is_derived": True}, headers=headers)
    assert res.status_code == 200, res.text

    # 수정 등록 — is_derived 미지정 → 부모(1) 승계.
    new_id = _import(client, headers, product, revision_of=rid)
    assert new_id != rid
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == new_id)
    assert mine["is_derived"] == 1


def test_explicit_is_derived_wins_over_inheritance():
    """명시적 ImportRequest.is_derived=True 가 부모(0) 승계보다 우선한다."""
    client = _client()
    headers = _login(client)
    product = f"EID{_uid()}"
    rid = _import(client, headers, product)  # 부모 기본 0
    # 수정 등록에서 명시적으로 is_derived=True 지정.
    new_id = _import(client, headers, product, revision_of=rid, is_derived=True)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == new_id)
    assert mine["is_derived"] == 1


def test_new_recipe_defaults_is_derived_zero():
    """비개정 신규 레시피는 is_derived=0 으로 시작(기본값)."""
    client = _client()
    headers = _login(client)
    product = f"NID{_uid()}"
    rid = _import(client, headers, product)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == rid)
    assert mine["is_derived"] == 0


# ── PUT /recipes/{id}/derived: auth + 토글 + 반영 ──

def test_manager_toggles_is_derived_reflected_in_payloads():
    """책임자가 is_derived=true → 200, 관리 목록·상세 모두에 반영."""
    client = _client()
    headers = _login(client)
    product = f"TID{_uid()}"
    rid = _import(client, headers, product)

    res = client.put(f"/api/recipes/{rid}/derived", json={"is_derived": True}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["is_derived"] is True

    # 관리 목록(GET /api/recipes)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    assert next(it for it in items if it["id"] == rid)["is_derived"] == 1

    # 상세(GET /api/recipes/{id}/detail)
    detail = client.get(f"/api/recipes/{rid}/detail").json()
    assert detail["is_derived"] == 1

    # 토글 → false 로 되돌림.
    res2 = client.put(f"/api/recipes/{rid}/derived", json={"is_derived": False}, headers=headers)
    assert res2.status_code == 200, res2.text
    assert res2.json()["is_derived"] is False
    detail2 = client.get(f"/api/recipes/{rid}/detail").json()
    assert detail2["is_derived"] == 0


def test_is_derived_in_history():
    """버전 이력(GET /api/recipes/{id}/history) 항목에 is_derived 가 노출된다."""
    client = _client()
    headers = _login(client)
    product = f"HID{_uid()}"
    rid = _import(client, headers, product)
    client.put(f"/api/recipes/{rid}/derived", json={"is_derived": True}, headers=headers)
    new_id = _import(client, headers, product, revision_of=rid)  # 승계 → 1

    hist = client.get(f"/api/recipes/{new_id}/history").json()["items"]
    by_id = {it["id"]: it for it in hist}
    assert by_id[rid]["is_derived"] is True
    assert by_id[new_id]["is_derived"] is True


def test_non_manager_blocked_from_is_derived():
    """미로그인(또는 비책임자) → 401 또는 403. use-reactor/category 엔드포인트와 동일 기대."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"BID{_uid()}")  # 시드만 책임자로

    blocked = client.put(f"/api/recipes/{rid}/derived", json={"is_derived": True})
    assert blocked.status_code in (401, 403)


def test_is_derived_invalid_body_rejected():
    """is_derived 가 bool 이 아니거나 누락 → 400."""
    client = _client()
    headers = _login(client)
    rid = _import(client, headers, f"IID{_uid()}")
    # 문자열
    assert client.put(
        f"/api/recipes/{rid}/derived", json={"is_derived": "yes"}, headers=headers
    ).status_code == 400
    # 누락
    assert client.put(
        f"/api/recipes/{rid}/derived", json={}, headers=headers
    ).status_code == 400


def test_is_derived_unknown_recipe_404():
    """존재하지 않는 레시피 id → 404."""
    client = _client()
    headers = _login(client)
    res = client.put("/api/recipes/999999/derived", json={"is_derived": True}, headers=headers)
    assert res.status_code == 404


def test_is_derived_independent_of_use_reactor():
    """is_derived 와 use_reactor 는 독립 — 한쪽을 켜도 다른 쪽은 그대로(디커플링 확인).

    use_reactor=true 로 저장한 뒤 is_derived 를 켜도 use_reactor 값은 변하지 않고,
    그 반대도 마찬가지다.
    """
    client = _client()
    headers = _login(client)
    product = f"IND{_uid()}"
    # use_reactor=true 로 등록 — is_derived 는 기본 0.
    rid = _import(client, headers, product, use_reactor=True)
    items = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine = next(it for it in items if it["id"] == rid)
    assert mine["use_reactor"] == 1
    assert mine["is_derived"] == 0  # use_reactor 를 켰다고 is_derived 가 켜지지 않는다.

    # 이제 is_derived 만 켠다 — use_reactor 값은 그대로 1.
    res = client.put(f"/api/recipes/{rid}/derived", json={"is_derived": True}, headers=headers)
    assert res.status_code == 200, res.text
    items2 = client.get("/api/recipes", params={"search": product}).json()["items"]
    mine2 = next(it for it in items2 if it["id"] == rid)
    assert mine2["is_derived"] == 1
    assert mine2["use_reactor"] == 1  # is_derived 토글이 use_reactor 를 건드리지 않는다.
