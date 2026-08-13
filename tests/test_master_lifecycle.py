"""품목 마스터 유효/폐기(status) 수명주기.

배경: 마스터 임포트가 upsert-only 라 ERP 에서 사라진 코드가 영구 잔존했고,
폐기 코드를 현행과 구분할 수단이 없어 폐기 이력이 도구 소스에 하드코딩됐다
(unify_material_names.KEEP_STORED_CODES 의 AS0066 사례). status/retired_at 컬럼과
임포트 --retire-missing 이 그 데이터 구조다.

검증:
  1. 마이그레이션 — status/retired_at 컬럼이 생기고 재실행에 안전(멱등).
  2. retire_missing_codes — 이번 임포트에 없는 active 코드만 폐기,
     manual 행 불가침, kind 격리, 빈 임포트 집합이면 무동작(전량 폐기 사고 방어),
     dry_run 무변경.
  3. _upsert_master — 재등장한 폐기 코드는 active 로 부활(retired_at 소거).
  4. 마스터 검색 API — status 를 싣고 폐기를 뒤로 정렬한다.

스타일: 도구 함수는 tmp_path 의 마이그레이션된 sqlite 파일로 직접 검증
(tests/test_audit_item_codes.py 관행), 라우터는 test_item_code_integrity.py 의
클라이언트·로그인 헬퍼 패턴.
"""

import importlib
import sqlite3
import uuid

import pytest

import src.db.connection as dbconn
from src.db import init_db
from tools.import_item_codes import _upsert_master, retire_missing_codes


# ---------------- 헬퍼 ----------------


@pytest.fixture()
def fresh_db(tmp_path):
    """tmp_path 산하 Fresh DB 로 init_db() 실행 후 연결 제공(모듈 전역 치환 —
    test_item_code_master._new_conn 관행). 종료 시 전역을 복원해 다음 테스트의
    공유 DB 접근이 tmp 경로로 새지 않게 한다."""
    orig_dir, orig_path = dbconn.DATA_DIR, dbconn.DATABASE_PATH
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "irms.db"
    dbconn.DATA_DIR = db_dir
    dbconn.DATABASE_PATH = db_path
    init_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
        dbconn.DATA_DIR = orig_dir
        dbconn.DATABASE_PATH = orig_path


def _seed_master(conn, code, *, kind="material", source="code",
                 status="active", retired_at=None):
    conn.execute(
        "INSERT INTO item_code_master "
        "(code, name, kind, source, imported_at, status, retired_at) "
        "VALUES (?, ?, ?, ?, 't', ?, ?)",
        (code, f"품목{code}", kind, source, status, retired_at),
    )


def _status_of(conn, code):
    row = conn.execute(
        "SELECT COALESCE(status, 'active') AS s, retired_at "
        "FROM item_code_master WHERE code = ?", (code,)
    ).fetchone()
    return (row["s"], row["retired_at"]) if row else None


# ---------------- 1. 마이그레이션 ----------------


def test_migration_adds_status_columns_idempotently(fresh_db):
    from src.db.migrations import apply_schema_migrations

    conn = fresh_db
    # init_db 가 한 번 적용했고, 재실행에도 안전해야 한다(멱등).
    apply_schema_migrations(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(item_code_master)")}
    assert "status" in cols
    assert "retired_at" in cols
    # 기본값은 active — 기존 행이 폐기로 오인되지 않는다.
    conn.execute(
        "INSERT INTO item_code_master (code, name, kind, imported_at) "
        "VALUES ('AS0002', 'X', 'material', 't')"
    )
    assert _status_of(conn, "AS0002")[0] == "active"


# ---------------- 2. retire_missing_codes ----------------


def test_retire_missing_marks_absent_active_codes(fresh_db):
    conn = fresh_db
    _seed_master(conn, "AS0001")                       # 이번에도 등장 → 유지
    _seed_master(conn, "AS0002")                       # 사라짐 → 폐기
    _seed_master(conn, "MN0001", source="manual")      # manual → 불가침
    _seed_master(conn, "B0001", kind="product")        # 다른 kind → 불가침
    retired = retire_missing_codes(
        conn, kind="material", imported_codes={"AS0001"}, now="2026-08-13T00:00:00Z"
    )
    assert retired == ["AS0002"]
    assert _status_of(conn, "AS0001") == ("active", None)
    assert _status_of(conn, "AS0002") == ("retired", "2026-08-13T00:00:00Z")
    assert _status_of(conn, "MN0001")[0] == "active"   # manual 불가침
    assert _status_of(conn, "B0001")[0] == "active"    # kind 격리


def test_retire_missing_noop_on_empty_import_set(fresh_db):
    """빈 임포트 집합이면 아무것도 폐기하지 않는다 — 빈 파일 사고가 전 코드를
    일괄 폐기하는 것을 막는 안전장치."""
    conn = fresh_db
    _seed_master(conn, "AS0001")
    assert retire_missing_codes(
        conn, kind="material", imported_codes=set(), now="t"
    ) == []
    assert _status_of(conn, "AS0001")[0] == "active"


def test_retire_missing_dry_run_reports_without_change(fresh_db):
    conn = fresh_db
    _seed_master(conn, "AS0001")
    _seed_master(conn, "AS0002")
    would = retire_missing_codes(
        conn, kind="material", imported_codes={"AS0001"}, now="t", dry_run=True
    )
    assert would == ["AS0002"]                          # 예정 목록은 보고하되
    assert _status_of(conn, "AS0002")[0] == "active"    # 실제 변경은 없다


def test_retire_missing_skips_already_retired(fresh_db):
    """이미 폐기된 행은 다시 목록에 오르지 않는다(retired_at 이 매번 갱신되면
    '언제 폐기됐는가'라는 이력이 사라진다)."""
    conn = fresh_db
    _seed_master(conn, "AS0002", status="retired", retired_at="2026-01-01T00:00:00Z")
    assert retire_missing_codes(
        conn, kind="material", imported_codes={"AS0001"}, now="t"
    ) == []
    assert _status_of(conn, "AS0002") == ("retired", "2026-01-01T00:00:00Z")


# ---------------- 3. 재등장 → 부활 ----------------


def test_upsert_revives_retired_code(fresh_db):
    conn = fresh_db
    _seed_master(conn, "AS0066", status="retired", retired_at="2026-01-01T00:00:00Z")
    _upsert_master(
        conn, code="AS0066", name="PVP K90", spec=None, unit=None,
        kind="material", category_hint=None, source="code", imported_at="t2",
    )
    assert _status_of(conn, "AS0066") == ("active", None)


# ---------------- 4. 마스터 검색 API — status 노출·폐기 후순위 ----------------


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


@pytest.fixture()
def _seeded_search_rows():
    """공유 테스트 DB 에 검색 전용 마스터 행 2개(유효/폐기)를 심고 정리한다."""
    from src.db import get_connection, init_db

    init_db()
    marker = uuid.uuid4().hex[:6].upper()
    active_code, retired_code = f"AS9{marker[:3]}1", f"AS9{marker[:3]}2"
    name = f"수명주기검색{marker}"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO item_code_master (code, name, kind, source, imported_at, status, retired_at) "
            "VALUES (?, ?, 'material', 'code', 't', 'active', NULL)",
            (active_code, name),
        )
        conn.execute(
            "INSERT INTO item_code_master (code, name, kind, source, imported_at, status, retired_at) "
            "VALUES (?, ?, 'material', 'code', 't', 'retired', '2026-08-13T00:00:00Z')",
            (retired_code, name),
        )
        conn.commit()
    yield name, active_code, retired_code
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM item_code_master WHERE code IN (?, ?)",
            (active_code, retired_code),
        )
        conn.commit()


def test_master_search_exposes_status_and_sorts_retired_last(_seeded_search_rows):
    name, active_code, retired_code = _seeded_search_rows
    client = _client()
    _login(client)
    res = client.get("/api/item-codes/master", params={"q": name})
    assert res.status_code == 200, res.text
    items = [it for it in res.json()["items"] if it["name"] == name]
    assert [it["code"] for it in items] == [active_code, retired_code]  # 유효 우선
    by_code = {it["code"]: it for it in items}
    assert by_code[active_code]["status"] == "active"
    assert by_code[retired_code]["status"] == "retired"
