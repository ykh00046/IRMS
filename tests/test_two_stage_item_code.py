"""2단 제조(1차→2차) 가족의 품목코드 승계 + 중복검사 self 의미 + 최근 LOT 제안.

K-TOP/K-TOP-1 류 새 가족이 PB/PB-1 처럼 즉시 동작하도록 하는 서버 동작 회귀 방지:
 1. 2차 BOM 에서 인식된 1차 반제품 자재가 1차 레시피의 product_code 를 승계한다
    (import_parser — 마스터에 없어도 코드 표시가 되도록).
 2. 코드 충돌(409) 메시지는 보유자를 지목하고, 같은 대상 재지정은 막지 않는다(self).
 3. 최근 LOT 제안(GET /blend/recent-product-lots)은 자재명=1차 반제품명으로 해석되어
    새 가족에서도 1차 완료 기록의 LOT 을 즉시 노출한다.

test_recipe_stage1.py 의 client/management-login/import 헬퍼 패턴을 따른다.
"""

from __future__ import annotations

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    """각 테스트 후 이 모듈이 남긴 item_code_master 'manual' 행 삭제 — 공유 pytest DB 오염 방지.

    test_item_code_admin.py 와 동일 패턴. POST /materials(코드 지정)·set_material_code 가
    _ensure_master_entry 로 자동 생성하는 source='manual' 행을 남기면 import_parser 의 마스터
    존재 판정이 바뀌어 다른 파일(test_recipe_management 등)의 원료 자동 등록이 차단된다.
    """
    yield
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM item_code_master WHERE source = 'manual'")
        conn.commit()


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
    return uuid.uuid4().hex[:6].upper()


def _import(client, headers, raw, **extra):
    body = {"raw_text": raw}
    body.update(extra)
    res = client.post("/api/recipes/import", json=body, headers=headers)
    return res


def _ensure_materials(client, headers, *names):
    """원료 자재를 미리 등록(코드 없이) — 공유 pytest DB 의 마스터 상태와 무관하게

    임포트가 '마스터에 없는 품목'으로 차단되지 않도록. 이미 있으면 409 는 무시.
    """
    for n in names:
        client.post("/api/materials", json={"name": n}, headers=headers)


# ── 1. 1차 product_code 승계 → 2차 BOM 의 1차 반제품 자재 ──


def test_intermediate_material_inherits_stage1_product_code():
    """1차 레시피(code 지정)를 2차 BOM 에 자재로 쓰면 그 자재가 1차 코드를 승계한다."""
    client = _client()
    headers = _login(client)
    base = _uid()
    one = f"K{base}-1"
    two = f"K{base}"
    code = f"KC{base[:4]}"  # 영문 2자 + 영숫자 4자 = 형식 통과

    _ensure_materials(client, headers, f"원료{base}A", f"원료{base}B", f"원료{base}C")
    # 1차 등록(product_code 지정).
    r1 = _import(client, headers, f"반제품명\t원료{base}A\t원료{base}B\n{one}\t60\t40",
                 product_code=code)
    assert r1.status_code == 200, r1.text

    # 2차 등록 — 1차 반제품명을 자재 행으로 사용.
    r2 = _import(client, headers, f"반제품명\t{one}\t원료{base}C\n{two}\t500\t20")
    assert r2.status_code == 200, r2.text

    # 자재 목록에 1차 반제품 자재가 있고, 코드가 1차 레시피 코드로 채워져 있어야 한다.
    mats = client.get("/api/item-codes/materials", headers=headers).json()["items"]
    by_name = {m["name"]: m for m in mats}
    assert one in by_name, f"1차 반제품 자재가 자동 등록되지 않음: {list(by_name)}"
    assert by_name[one]["code"] == code, by_name[one]


def test_intermediate_without_code_stays_none():
    """1차 레시피에 코드가 없으면 2차 BOM 자재도 코드 없이 등록(회귀 방지 — 강제 코드 부여 안 함)."""
    client = _client()
    headers = _login(client)
    base = _uid()
    one = f"N{base}-1"
    two = f"N{base}"
    _ensure_materials(client, headers, f"원료{base}A")
    r1 = _import(client, headers, f"반제품명\t원료{base}A\n{one}\t100")
    assert r1.status_code == 200, r1.text
    r2 = _import(client, headers, f"반제품명\t{one}\n{two}\t500")
    assert r2.status_code == 200, r2.text
    mats = client.get("/api/item-codes/materials", headers=headers).json()["items"]
    by_name = {m["name"]: m for m in mats}
    assert one in by_name
    assert by_name[one]["code"] is None


def test_intermediate_code_clash_falls_back_to_none():
    """1차 코드를 이미 다른 자재가 쥐고 있으면(materials.code UNIQUE 충돌) 코드 없이 등록.

    parse_import_text 를 직접 호출해 제어된 스키마로 검증(UNIQUE 충돌 500 방지 경로).
    """
    import sqlite3

    from src.services import import_parser

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            unit_type TEXT NOT NULL DEFAULT 'weight', unit TEXT NOT NULL DEFAULT 'g',
            color_group TEXT NOT NULL DEFAULT 'none', category TEXT,
            is_active INTEGER NOT NULL DEFAULT 1, code TEXT
        );
        CREATE UNIQUE INDEX ux_mat_code ON materials(code) WHERE code IS NOT NULL;
        CREATE TABLE material_aliases (id INTEGER PRIMARY KEY, material_id INTEGER, alias_name TEXT);
        CREATE TABLE recipe_items (id INTEGER PRIMARY KEY, recipe_id INTEGER, material_id INTEGER);
        CREATE TABLE recipes (id INTEGER PRIMARY KEY AUTOINCREMENT, product_name TEXT,
            status TEXT DEFAULT 'completed', created_at TEXT, product_code TEXT);
        CREATE TABLE item_code_master (code TEXT PRIMARY KEY, name TEXT NOT NULL,
            kind TEXT NOT NULL, category_hint TEXT, is_active INTEGER DEFAULT 1);
        """
    )
    # 코드 CLASH1 을 이미 다른 자재가 점유.
    conn.execute("INSERT INTO materials (name, code) VALUES ('기존자재', 'CLASH1')")
    # 1차 반제품 레시피(completed)가 같은 코드를 product_code 로 가짐.
    conn.execute(
        "INSERT INTO recipes (product_name, status, product_code) VALUES ('INT-1', 'completed', 'CLASH1')"
    )
    # 원료A 를 미리 등록해 2차 파싱이 raw material 차단에 걸리지 않게 한다.
    conn.execute("INSERT INTO materials (name) VALUES ('원료A')")
    conn.commit()

    # 2차 BOM — INT-1 을 자재로 사용. INT-1 은 completed 레시피라 인식되지만 코드 CLASH1 이
    # 이미 자재에 점유돼 있으므로 코드 없이 폴백해야 한다(UNIQUE 충돌 500 없이).
    result = import_parser.parse_import_text(conn, "반제품명\tINT-1\t원료A\n2차\t50\t50")
    assert result["errors"] == [], result["errors"]
    matches = {m["name"]: m for m in result["material_matches"]}
    assert matches["INT-1"]["status"] == "recipe"
    assert matches["INT-1"]["code"] is None  # 충돌로 코드 없이 폴백
    # 실제 삽입된 INT-1 자재도 code 가 NULL 이어야 한다.
    row = conn.execute("SELECT code FROM materials WHERE name = 'INT-1'").fetchone()
    assert row["code"] is None


# ── 2. 코드 중복검사 self 의미 ──


def test_material_code_reassign_same_is_ok():
    """같은 자재에 같은 코드를 다시 지정하면 self 제외로 409 가 아니라 200."""
    client = _client()
    headers = _login(client)
    base = _uid()
    code = f"SC{base[:4]}"
    res = client.post("/api/materials", json={"name": f"자재{base}", "code": code}, headers=headers)
    assert res.status_code == 200, res.text
    mid = res.json()["id"]
    # 같은 코드 재지정.
    again = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert again.status_code == 200, again.text
    assert again.json()["code"] == code


def test_material_code_conflict_names_holder():
    """다른 자재가 쥔 코드를 새 자재에 주려 하면 409 + 보유 자재명이 메시지에 포함."""
    client = _client()
    headers = _login(client)
    base = _uid()
    code = f"HC{base[:4]}"
    holder = f"보유{base}"
    res = client.post("/api/materials", json={"name": holder, "code": code}, headers=headers)
    assert res.status_code == 200, res.text
    # 같은 코드로 다른 새 자재 등록 → 409, 보유자 지목.
    dup = client.post("/api/materials", json={"name": f"신규{base}", "code": code}, headers=headers)
    assert dup.status_code == 409, dup.text
    assert holder in dup.json()["detail"]
    assert "사용 중인 코드" in dup.json()["detail"]


def test_material_code_force_move():
    """force=true 면 기존 보유 자재에서 코드를 떼어 새 자재로 이동(수정/이동 경로)."""
    client = _client()
    headers = _login(client)
    base = _uid()
    code = f"FC{base[:4]}"
    holder = f"원보유{base}"
    r1 = client.post("/api/materials", json={"name": holder, "code": code}, headers=headers)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/materials", json={"name": f"새자재{base}", "code": code, "force": True}, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["moved_from"] == holder


# ── 3. 최근 LOT 제안 — 새 가족 즉시 동작(이름 기반) ──


def test_recent_product_lots_resolves_by_name_for_new_family():
    """새 가족의 1차 완료 배합 기록 LOT 이 recent-product-lots 에 즉시 노출된다(이름 매칭)."""
    client = _client()
    headers = _login(client)
    base = _uid()
    one = f"R{base}-1"

    # 1차 완료 배합 기록을 직접 삽입(배합 생성 세부 검증과 무관 — LOT 노출만 확인).
    import src.db.connection as dbconn
    with dbconn.get_connection() as conn:
        conn.execute(
            "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
            "total_amount, status, created_at) VALUES (?, ?, ?, ?, ?, 'completed', ?)",
            (f"{one}26072401", one, "홍길동", "2026-07-24", 1234.0, "2026-07-24T00:00:00Z"),
        )
        conn.commit()

    res = client.get("/api/blend/recent-product-lots", params={"names": one}, headers=headers)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert one in items, items
    assert items[one][0]["lot"] == f"{one}26072401"
    assert items[one][0]["total"] == 1234.0


def test_recent_product_lots_empty_when_no_records():
    """완료 기록이 없으면 그 이름 키는 결과에 없다(빈 제안 — 정상)."""
    client = _client()
    headers = _login(client)
    res = client.get(
        "/api/blend/recent-product-lots", params={"names": f"NONE{_uid()}-1"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["items"] == {}
