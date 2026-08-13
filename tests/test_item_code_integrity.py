"""품목코드 무결성 — 등록 경로 동의어 충돌·감사 old_code·교차 중복·NOCASE 인덱스.

검토에서 확정된 틈새 4건의 회귀 테스트.

  1. POST /materials 가 normalize_token 기준 이름 충돌(자재명·동의어)을 막는다.
     ('HEMA (Lotte)' 와 'HEMA(Lotte)' 는 lower() 로는 다른 이름이지만 해석기에서는
      같은 이름이라, 한쪽만 코드를 쥐면 실적 집계가 갈린다.)
  2. 코드 변경 감사 로그에 이전 코드(old_code)가 남는다(자재·반제품 양쪽).
  3. 자재 코드와 반제품 코드가 같은 코드를 동시에 쥐지 못한다(force 로도 우회 불가).
     단 **이름이 같은** 1차 반제품↔중간체 자재 쌍은 정상 경로라 막지 않는다.
  4. materials.code 유니크 인덱스가 NOCASE 라 'ac0101'/'AC0101' 공존이 불가능하다.

스타일은 tests/test_item_code_admin.py(클라이언트·로그인·시드 헬퍼)와
tests/test_item_code_master.py(tmp_path DB 마이그레이션 검증)를 그대로 따른다.
"""

import importlib
import json
import logging
import sqlite3
import uuid

import pytest

import src.db.connection as dbconn
from src.db import init_db
from src.db.migrations import ensure_materials_code_nocase_unique


@pytest.fixture(autouse=True)
def _cleanup_manual_master():
    """이 모듈이 코드 부여로 만든 item_code_master manual 행 정리(공유 테스트 DB 보호).

    test_item_code_admin.py 의 동일 픽스처와 같은 이유 — manual 행이 남으면
    import_parser 의 마스터 존재 판정이 바뀌어 다른 테스트가 회귀한다.
    """
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM item_code_master WHERE source = 'manual'")
        conn.commit()


# ---------------- 공통 헬퍼 (test_item_code_admin.py 패턴) ----------------


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client, username="admin", password="admin"):
    res = client.post(
        "/api/auth/management-login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _short(n=5):
    return uuid.uuid4().hex[:n].upper()


def _seed_material(conn, name, code=None):
    cur = conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code) "
        "VALUES (?, 'weight', 'g', 'none', NULL, 1, ?)",
        (name, code),
    )
    conn.commit()
    return cur.lastrowid


def _seed_recipe(conn, product_name, product_code=None):
    """단일 레시피(체인 루트) 직접 INSERT → id."""
    rid = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, product_code) "
        "VALUES (?, ?, 'completed', 't', '2026-08-01T00:00:00Z', ?)",
        (product_name, product_name, product_code),
    ).lastrowid
    conn.commit()
    return rid


def _audit_details(conn, action, target_id):
    row = conn.execute(
        "SELECT details_json FROM audit_logs "
        "WHERE action = ? AND target_id = ? ORDER BY id DESC LIMIT 1",
        (action, str(target_id)),
    ).fetchone()
    assert row is not None, f"{action} 감사 로그가 없습니다."
    return json.loads(row["details_json"] or "{}")


# ================================================================
# 1. POST /materials — 이름 정규화·동의어 충돌
# ================================================================


def test_create_material_rejects_symbol_only_name():
    """기호만 남는 이름(normalize_token 이 빈 문자열) → 400.

    이름 변경(PUT /materials/{id}/name)에는 있던 규칙이 등록에는 없었다.
    """
    client = _client()
    headers = _login(client)

    res = client.post("/api/materials", json={"name": "---"}, headers=headers)
    assert res.status_code == 400, res.text
    assert "이름" in res.json()["detail"]


def test_create_material_rejects_normalized_duplicate_name():
    """공백·괄호만 다른 이름('HEMA (X)' vs 'HEMA(X)') → 409, 기존 자재명 노출.

    lower() 비교만 하던 종전 검사는 이 쌍을 통과시켜, 해석기(normalize_token)에서는
    하나로 합쳐질 두 자재가 각각 등록됐다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    existing = f"HEMA ({s})"
    with get_connection() as conn:
        _seed_material(conn, existing)

    res = client.post(
        "/api/materials", json={"name": f"HEMA({s})"}, headers=headers
    )
    assert res.status_code == 409, res.text
    assert existing in res.json()["detail"]


def test_create_material_rejects_other_material_alias():
    """다른 자재의 동의어와 같은 이름으로 등록 → 409(소유 자재명 포함)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    owner = f"본자재{s}"
    with get_connection() as conn:
        owner_id = _seed_material(conn, owner)

    alias = f"OLD NAME {s}"
    res = client.post(
        f"/api/materials/{owner_id}/aliases",
        json={"alias_name": alias},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    # 공백만 다른 같은 이름(normalize_token 동일) → 409.
    res = client.post(
        "/api/materials", json={"name": f"OLDNAME{s}"}, headers=headers
    )
    assert res.status_code == 409, res.text
    assert owner in res.json()["detail"]


def test_create_material_normal_name_still_ok():
    """회귀 가드 — 충돌 없는 정상 이름은 그대로 등록된다."""
    client = _client()
    headers = _login(client)

    s = _short()
    res = client.post(
        "/api/materials", json={"name": f"정상자재{s}"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == f"정상자재{s}"


# ================================================================
# 2. 감사 로그 old_code
# ================================================================


def test_material_code_set_audit_records_old_code():
    """PUT /materials/{id}/code — 감사 details 에 이전 코드(old_code)가 남는다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    old_code = f"AS{s}1"
    new_code = f"AS{s}2"
    with get_connection() as conn:
        mid = _seed_material(conn, f"코드변경자재{s}", code=old_code)

    res = client.put(
        f"/api/materials/{mid}/code", json={"code": new_code}, headers=headers
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        details = _audit_details(conn, "material_code_set", mid)
    assert details["code"] == new_code
    assert details["old_code"] == old_code


def test_material_code_clear_audit_records_old_code():
    """코드 해제(null)도 이전 코드를 남긴다 — 감사만 보고 되돌릴 수 있어야 한다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    old_code = f"AS{s}3"
    with get_connection() as conn:
        mid = _seed_material(conn, f"해제자재{s}", code=old_code)

    res = client.put(f"/api/materials/{mid}/code", json={"code": None}, headers=headers)
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        details = _audit_details(conn, "material_code_set", mid)
    assert details["code"] is None
    assert details["old_code"] == old_code


def test_recipe_product_code_set_audit_records_old_code():
    """PUT /recipes/{id}/product-code — 감사 details 에 이전 코드(old_code)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    old_code = f"BC{s}1"
    new_code = f"BC{s}2"
    product = f"PAUD{_uid()}"
    with get_connection() as conn:
        rid = _seed_recipe(conn, product, product_code=old_code)

    res = client.put(
        f"/api/recipes/{rid}/product-code",
        json={"product_code": new_code},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        details = _audit_details(conn, "recipe_product_code_set", rid)
    assert details["product_code"] == new_code
    assert details["old_code"] == old_code


# ================================================================
# 3. 자재 코드 ↔ 반제품 코드 교차 중복 차단
# ================================================================


def test_material_code_blocked_when_product_holds_it():
    """반제품이 쥔 코드는 자재에 부여할 수 없다 — 409, force 로도 우회 불가."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"BC{s}5"
    product = f"PHOLD{_uid()}"
    with get_connection() as conn:
        _seed_recipe(conn, product, product_code=code)
        mid = _seed_material(conn, f"교차자재{s}")

    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "반제품 품목코드로 사용 중" in detail
    assert product in detail

    # force 로도 뚫리지 않는다(교차 중복은 '이동' 개념이 없다).
    res = client.put(
        f"/api/materials/{mid}/code",
        json={"code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 409, res.text

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT code FROM materials WHERE id = ?", (mid,)
        ).fetchone()["code"]
    assert stored is None


def test_create_material_blocked_when_product_holds_code():
    """POST /materials 의 코드 지정 경로도 같은 규칙(409)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"BC{s}6"
    product = f"PNEW{_uid()}"
    with get_connection() as conn:
        _seed_recipe(conn, product, product_code=code)

    res = client.post(
        "/api/materials",
        json={"name": f"신규교차{s}", "code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 409, res.text
    assert "반제품 품목코드로 사용 중" in res.json()["detail"]
    assert product in res.json()["detail"]


def test_product_code_blocked_when_material_holds_it():
    """자재가 쥔 코드는 반제품에 부여할 수 없다 — 409(자재명 포함)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}7"
    material_name = f"선점자재{s}"
    product = f"PMAT{_uid()}"
    with get_connection() as conn:
        _seed_material(conn, material_name, code=code)
        rid = _seed_recipe(conn, product)

    res = client.put(
        f"/api/recipes/{rid}/product-code",
        json={"product_code": code},
        headers=headers,
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "자재 품목코드로 사용 중" in detail
    assert material_name in detail


def test_same_name_intermediate_pair_is_not_a_conflict():
    """회귀 가드 — 이름이 같은 1차 반제품↔중간체 자재 쌍은 교차 중복이 아니다.

    임포트가 2차 BOM 의 1차 반제품 자재에 1차 레시피 코드를 일부러 승계하므로
    (import_parser.completed_recipe_codes), 이 쌍까지 막으면 코드 재지정이 영구히
    409 가 된다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"BC{s}8"
    name = f"KINT{s}-1"
    with get_connection() as conn:
        rid = _seed_recipe(conn, name, product_code=code)
        mid = _seed_material(conn, name, code=code)

    # 반제품 쪽 재지정 — 같은 이름 자재가 쥐고 있어도 200.
    res = client.put(
        f"/api/recipes/{rid}/product-code",
        json={"product_code": code},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    # 자재 쪽 재지정 — 같은 이름 반제품이 쥐고 있어도 200.
    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 200, res.text


def test_import_explicit_product_code_blocked_when_material_holds_it():
    """레시피 임포트의 명시 코드 경로도 자재 점유 코드를 거부(409, 자재명 포함)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}9"
    material_name = f"임포트선점{s}"
    raw_name = f"원료{s}"
    with get_connection() as conn:
        _seed_material(conn, material_name, code=code)
        _seed_material(conn, raw_name)

    res = client.post(
        "/api/recipes/import",
        json={
            "raw_text": f"반제품명\t{raw_name}\nPIMP{s}\t100",
            "product_code": code,
        },
        headers=headers,
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "자재 품목코드로 사용 중" in detail
    assert material_name in detail


# ================================================================
# 4. materials.code NOCASE 유니크 인덱스 (마이그레이션)
# ================================================================


def _fresh_db(tmp_path):
    """tmp_path 산하 Fresh DB 로 init_db() 후 별도 연결 반환.

    test_item_code_master.py 의 _new_conn 과 동일 패턴(모듈 전역 치환 — 루트
    conftest 의 _restore_db_path_bindings 픽스처가 테스트 후 복구한다).
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


def _index_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_materials_code'"
    ).fetchone()
    return (row["sql"] or "") if row else ""


def _insert(conn, name, code=None):
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, is_active, code) "
        "VALUES (?, 'weight', 'g', 'none', 1, ?)",
        (name, code),
    )
    conn.commit()


def _close(conn):
    """tmp DB 연결 정리 — 실패한 INSERT(IntegrityError)가 남긴 트랜잭션을 되돌리고 닫는다.

    안 닫으면 그 연결이 쓰기 잠금을 쥔 채 남아, 뒤따르는 픽스처/테스트가
    'database is locked' 로 죽는다.
    """
    conn.rollback()
    conn.close()


def test_materials_code_index_is_nocase_after_init(tmp_path):
    """init_db 직후 idx_materials_code 가 NOCASE — 대소문자만 다른 코드 INSERT 실패."""
    conn = _fresh_db(tmp_path)
    assert "NOCASE" in _index_sql(conn).upper()

    _insert(conn, "자재대문자", "AC0101")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "자재소문자", "ac0101")

    # NULL 은 여러 개 허용(부분 유니크 인덱스 유지).
    conn.rollback()
    _insert(conn, "무코드1", None)
    _insert(conn, "무코드2", None)
    _close(conn)


def test_migration_normalizes_lowercase_and_empty_codes(tmp_path):
    """소문자 코드는 대문자로, 빈 문자열 코드는 NULL 로 정리된다."""
    conn = _fresh_db(tmp_path)
    _insert(conn, "소문자자재", "ac0202")
    _insert(conn, "빈코드자재", "")
    _insert(conn, "공백코드자재", "  ")

    ensure_materials_code_nocase_unique(conn)
    conn.commit()

    rows = {
        r["name"]: r["code"]
        for r in conn.execute("SELECT name, code FROM materials").fetchall()
    }
    assert rows["소문자자재"] == "AC0202"
    assert rows["빈코드자재"] is None
    assert rows["공백코드자재"] is None
    _close(conn)


def test_migration_is_idempotent(tmp_path):
    """여러 번 실행해도 안전 — 인덱스 정의·코드 값이 그대로다(서버 기동마다 재실행)."""
    conn = _fresh_db(tmp_path)
    _insert(conn, "멱등자재", "ac0303")

    ensure_materials_code_nocase_unique(conn)
    conn.commit()
    first_sql = _index_sql(conn)

    for _ in range(3):
        ensure_materials_code_nocase_unique(conn)
        conn.commit()

    assert _index_sql(conn) == first_sql
    assert "NOCASE" in first_sql.upper()
    assert (
        conn.execute(
            "SELECT code FROM materials WHERE name = '멱등자재'"
        ).fetchone()["code"]
        == "AC0303"
    )
    _close(conn)


def test_apply_schema_migrations_upgrades_binary_index(tmp_path):
    """구버전 DB(BINARY 인덱스) 를 apply_schema_migrations 가 NOCASE 로 승급시킨다.

    운영 DB 는 이미 BINARY 인덱스로 만들어져 있으므로, 기동 시 마이그레이션 경유로
    승급되는지가 실제 경로다(헬퍼 직접 호출이 아니라).
    """
    from src.db.migrations import apply_schema_migrations

    conn = _fresh_db(tmp_path)
    conn.execute("DROP INDEX idx_materials_code")
    conn.execute(
        "CREATE UNIQUE INDEX idx_materials_code ON materials(code) WHERE code IS NOT NULL"
    )
    _insert(conn, "구버전자재", "ac0606")

    apply_schema_migrations(conn)
    conn.commit()

    assert "NOCASE" in _index_sql(conn).upper()
    assert (
        conn.execute(
            "SELECT code FROM materials WHERE name = '구버전자재'"
        ).fetchone()["code"]
        == "AC0606"
    )
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, "중복시도", "Ac0606")
    _close(conn)


def test_migration_keeps_index_and_warns_when_case_conflict_remains(tmp_path, caplog):
    """대소문자 충돌쌍이 남아 있으면 인덱스를 바꾸지 않고 경고만 — 기동 실패 방지.

    충돌쌍은 어느 쪽을 살릴지 사람이 정해야 하므로 값도 건드리지 않는다.
    충돌과 무관한 소문자 코드는 그대로 정규화된다.
    """
    conn = _fresh_db(tmp_path)
    # 기존 NOCASE 인덱스를 구버전(BINARY)으로 되돌려 충돌쌍을 만들 수 있게 한다.
    conn.execute("DROP INDEX idx_materials_code")
    conn.execute(
        "CREATE UNIQUE INDEX idx_materials_code ON materials(code) WHERE code IS NOT NULL"
    )
    _insert(conn, "충돌A", "ac0404")
    _insert(conn, "충돌B", "AC0404")
    _insert(conn, "무관자재", "as0505")

    with caplog.at_level(logging.WARNING, logger="src.db.migrations"):
        ensure_materials_code_nocase_unique(conn)
    conn.commit()

    rows = {
        r["name"]: r["code"]
        for r in conn.execute("SELECT name, code FROM materials").fetchall()
    }
    # 충돌쌍은 손대지 않는다.
    assert rows["충돌A"] == "ac0404"
    assert rows["충돌B"] == "AC0404"
    # 충돌과 무관한 행은 정규화된다.
    assert rows["무관자재"] == "AS0505"
    # 인덱스는 기존(BINARY) 유지 — 재생성 시도 시 CREATE 가 실패해 기동이 죽는다.
    assert "NOCASE" not in _index_sql(conn).upper()
    assert "AC0404" in caplog.text
    _close(conn)
