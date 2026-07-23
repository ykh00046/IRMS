"""POST /api/materials — 신규 자재 등록(A6) 엔드포인트 검증.

품목코드 관리 화면에서 운영자가 새 자재를 만드는 흐름을 커버한다.
test_item_code_admin.py 의 in-memory 클라이언트/로그인/헬퍼 패턴을 그대로 따른다.

커버:
  (a) code 와 함께 등록 성공 — defaults(unit='g', category='미분류', is_active=1) 확인.
  (b) code 없이 등록 성공 — code is NULL.
  (c) 빈 이름 → 400.
  (d) 자재명 중복(대소문자 무시) → 409.
  (e) code 형식 위반("A1") → 400.
  (f) 다른 자재가 이미 쓰는 code → 409(자재명 포함).
"""

import importlib
import sqlite3
import uuid

import pytest


# ---------------- 테스트 격리: item_code_master manual/erp 행 정리 ----------------


@pytest.fixture(autouse=True)
def _cleanup_master_rows():
    """각 테스트 종료 후 이 모듈이 만든 item_code_master 행을 삭제.

    테스트 DB(.tmp-tests/pytest-data/irms.db)는 실행 간 유지된다. 이 모듈의 테스트는
    POST /api/materials·PUT /api/materials/{id}/code·PUT /api/recipes/{id}/product-code
    를 두드리고, 백엔드의 _ensure_master_entry 가 코드 부여 시 source='manual' 인
    item_code_master 행을 자동으로 채운다. 또한 test_existing_master_row_not_modified
    _on_assign 가 source='erp' 인 시뮬레이션 행을 직접 심는다. 이 둘을 남기면 마스터
    테이블이 비어있지 않게 되어 import_parser 의 미리보기 자동 등록 정책이 바뀌고,
    test_route_coverage::test_import_preview_has_no_side_effect_on_materials 가
    결정론적으로 깨진다.

    삭제 대상 source:
      - 'manual': _ensure_master_entry 가 만든 행(실제 ERP 임포트와 구분됨).
      - 'erp':    테스트가 직접 심은 ERP 시뮬레이션 행. 실제 ERP Excel 임포트는
                  파일명(code.xlsx 등)을 source 로 쓰므로 'erp' 는 테스트 전용이다.

    마이그 전 DB(테이블 없음)에서는 OperationalError 를 조용히 무시한다
    — search_item_code_master 의 방어 패턴과 동일.
    """
    yield
    from src.db import get_connection

    try:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM item_code_master WHERE source IN ('manual', 'erp')"
            )
            conn.commit()
    except sqlite3.OperationalError:
        pass


# ---------------- 공통 픽스처/헬퍴 (기존 테스트 패턴 그대로) ----------------


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _login(client, username="admin", password="admin"):
    """책임자 로그인 → CSRF 헤더 반환."""
    res = client.post(
        "/api/auth/management-login", json={"username": username, "password": password}
    )
    assert res.status_code == 200, res.text
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _short(n=5):
    """짧은 고유 접미사(품목코드용 — 'AS'+5자 = 7자, digit 붙여도 안전)."""
    return uuid.uuid4().hex[:n].upper()


def _seed_material(conn, name, code=None):
    """materials 행 직접 삽입 → id 반환(기존 테스트의 INSERT 패턴)."""
    cur = conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code) "
        "VALUES (?, 'weight', 'g', 'none', NULL, 1, ?)",
        (name, code),
    )
    conn.commit()
    return cur.lastrowid


def _seed_master(conn, code, name, kind, source="erp", category_hint="원자재"):
    """item_code_master 행 직접 삽입 — ERP 임포트 시뮬레이션용.

    test_item_code_admin.py 의 _seed_master_row 와 비슷하지만 source 인자를 받아
    manual 행과 ERP 행을 구분할 수 있다.
    """
    conn.execute(
        "INSERT INTO item_code_master (code, name, kind, category_hint, source, imported_at) "
        "VALUES (?, ?, ?, ?, ?, '2026-07-01T00:00:00Z')",
        (code, name, kind, category_hint, source),
    )
    conn.commit()


def _build_recipe_chain(conn, product):
    """원본→개정 2단 체인 직접 INSERT → (v1, v2) id.

    test_item_code_admin.py 의 _build_chain 을 단순화한 버전.
    """
    v1 = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
        "VALUES (?, ?, 'completed', 't', '2026-07-01T00:00:00Z')",
        (product, product),
    ).lastrowid
    v2 = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, revision_of) "
        "VALUES (?, ?, 'completed', 't', '2026-07-02T00:00:00Z', ?)",
        (product, product, v1),
    ).lastrowid
    conn.commit()
    return v1, v2


# ---------------- (a) code 와 함께 등록 성공 + defaults ----------------


def test_create_material_with_code_succeeds_and_defaults():
    """code 와 함께 등록 → 200, 행 존재 + 기본 defaults(unit='g' 등)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    name = f"신규자재{s}"
    code = f"AS{s}1"

    res = client.post("/api/materials", json={"name": name, "code": code}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["name"] == name
    assert body["code"] == code
    new_id = body["id"]
    assert isinstance(new_id, int)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT name, unit_type, unit, color_group, category, is_active, code "
            "FROM materials WHERE id = ?",
            (new_id,),
        ).fetchone()
    assert row is not None
    assert row["name"] == name
    assert row["code"] == code
    # import_parser._auto_register_material 과 동일한 기본값.
    assert row["unit_type"] == "weight"
    assert row["unit"] == "g"
    assert row["color_group"] == "none"
    assert row["category"] == "미분류"
    assert row["is_active"] == 1


# ---------------- (b) code 없이 등록 성공 → NULL ----------------


def test_create_material_without_code_succeeds_null():
    """code 생략 → 등록 성공, code is NULL."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    name = f"코드없음{_uid()}"

    res = client.post("/api/materials", json={"name": name}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] is None
    new_id = body["id"]

    with get_connection() as conn:
        row = conn.execute(
            "SELECT code FROM materials WHERE id = ?", (new_id,)
        ).fetchone()
    assert row is not None
    assert row["code"] is None


# ---------------- (c) 빈 이름 → 400 ----------------


def test_create_material_empty_name_400():
    """빈 이름(또는 공백) → 400."""
    client = _client()
    headers = _login(client)

    # 빈 문자열
    res = client.post("/api/materials", json={"name": ""}, headers=headers)
    assert res.status_code == 400
    assert "자재명" in res.json()["detail"]

    # 공백만
    res = client.post("/api/materials", json={"name": "   "}, headers=headers)
    assert res.status_code == 400

    # name 키 자체 누락
    res = client.post("/api/materials", json={}, headers=headers)
    assert res.status_code == 400


# ---------------- (d) 자재명 중복(대소문자 무시) → 409 ----------------


def test_create_material_duplicate_name_case_insensitive_409():
    """이미 등록된 이름(대소문자만 다른 경우 포함) → 409."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    existing = f"DUPMAT{_uid()}"
    with get_connection() as conn:
        _seed_material(conn, existing)

    # 동일 이름
    res = client.post("/api/materials", json={"name": existing}, headers=headers)
    assert res.status_code == 409

    # 대소문자만 바꿔서(영문 접두사 영역) — 동일 판정되어야 한다.
    res = client.post(
        "/api/materials", json={"name": existing.lower()}, headers=headers
    )
    assert res.status_code == 409


# ---------------- (e) code 형식 위반 → 400 ----------------


def test_create_material_invalid_code_format_400():
    """code 형식(영문 2자 + 영숫자 2~8자) 위반 → 400."""
    client = _client()
    headers = _login(client)

    name = f"형식오류{_uid()}"
    # "A1" — 영문 1자 + 숫자 1자 = 형식 위반.
    res = client.post("/api/materials", json={"name": name, "code": "A1"}, headers=headers)
    assert res.status_code == 400


# ---------------- (f) 다른 자재가 쓰는 code → 409 ----------------


def test_create_material_duplicate_code_409():
    """다른 자재가 같은 code 를 쓰면 409(자재명 포함)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}7"
    other_name = f"선점자재{s}"
    with get_connection() as conn:
        _seed_material(conn, other_name, code=code)

    new_name = f"새자재{s}"
    res = client.post(
        "/api/materials", json={"name": new_name, "code": code}, headers=headers
    )
    assert res.status_code == 409
    assert other_name in res.json()["detail"]


# ---------------- (g) POST /materials force=true → 코드 이동 등록 ----------------


def test_create_material_force_moves_code_from_previous_holder():
    """(d) POST /materials force=true → 새 자재 생성 + 기존 보유 자재 코드 해제.

    새 자재가 코드를 갖고, 이전 보유 자재의 code 는 NULL. 응답 moved_from 은 이전
    보유 자재명. audit: material_code_cleared(이전 보유) + material_created(새 자재).
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}8"
    holder_name = f"기존보유{s}"
    with get_connection() as conn:
        holder_id = _seed_material(conn, holder_name, code=code)

    new_name = f"이동신규{s}"
    res = client.post(
        "/api/materials",
        json={"name": new_name, "code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == new_name
    assert body["code"] == code
    assert body["moved_from"] == holder_name
    new_id = body["id"]

    with get_connection() as conn:
        # 새 자재가 코드 보유.
        new_row = conn.execute(
            "SELECT code FROM materials WHERE id = ?", (new_id,)
        ).fetchone()
        assert new_row["code"] == code
        # 이전 보유 자재는 코드 해제(NULL).
        holder_row = conn.execute(
            "SELECT code FROM materials WHERE id = ?", (holder_id,)
        ).fetchone()
        assert holder_row["code"] is None
        # audit: material_code_cleared(이전 보유, target_id=holder_id).
        cleared = conn.execute(
            "SELECT action, details_json FROM audit_logs "
            "WHERE action='material_code_cleared' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(holder_id),),
        ).fetchone()
        assert cleared is not None
        assert code in (cleared["details_json"] or "")
        # audit: material_created(새 자재).
        created = conn.execute(
            "SELECT action, details_json FROM audit_logs "
            "WHERE action='material_created' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(new_id),),
        ).fetchone()
        assert created is not None
        assert holder_name in (created["details_json"] or "")


# ---------------- item_code_master 동기화(A6/A3/A4 → _ensure_master_entry) ----------------


def _master_row(conn, code):
    return conn.execute(
        "SELECT code, name, kind, source, category_hint, spec, unit "
        "FROM item_code_master WHERE code = ?",
        (code,),
    ).fetchone()


def test_create_material_inserts_manual_master_row():
    """(a) create_material + 새 code → item_code_master 에 source='manual',
    kind='material' 행 추가. ERP Excel 재임포트 없이 제안 검색에 노출되게."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    name = f"마스터자재{s}"
    code = f"AS{s}2"

    res = client.post("/api/materials", json={"name": name, "code": code}, headers=headers)
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, code)
    assert row is not None
    assert row["kind"] == "material"
    assert row["source"] == "manual"
    assert row["name"] == name
    assert row["spec"] is None
    assert row["unit"] is None
    assert row["category_hint"] is None


def test_set_material_code_inserts_manual_master_row():
    """(b) set_material_code(A3) + 새 code → master 행 추가(kind='material')."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}3"
    name = f"A3자재{s}"
    with get_connection() as conn:
        mid = _seed_material(conn, name)

    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, code)
    assert row is not None
    assert row["kind"] == "material"
    assert row["source"] == "manual"
    assert row["name"] == name


def test_existing_master_row_not_modified_on_assign():
    """(c) 코드가 이미 item_code_master 에 있을 때(ERP 임포트분) 부여해도
    기존 master 행은 절대 수정되지 않는다 — ERP 가 authoritative.

    ERP 행(name='ERP명', source='erp', category_hint='원자재')을 심어두고,
    그 코드로 create_material 을 시도하면 materials.code 충돌(409)이 나므로,
    대신 자재는 다른 코드로 만든 뒤 set_material_code 로 ERP 코드를 부여한다.
    그래도 master 행은 그대로여야 한다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    erp_code = f"AS{s}4"
    erp_name = f"ERP명{s}"
    with get_connection() as conn:
        _seed_master(conn, erp_code, erp_name, "material", source="erp", category_hint="원자재")
        # 자재는 코드 없이 만들어 두고, 나중에 ERP 코드를 부여한다.
        mid = _seed_material(conn, f"운영자재{s}")

    res = client.put(f"/api/materials/{mid}/code", json={"code": erp_code}, headers=headers)
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, erp_code)
        # ERP 임포트분이 그대로 — source/name/category_hint 변경 없음.
        assert row["source"] == "erp"
        assert row["name"] == erp_name
        assert row["category_hint"] == "원자재"
        # 행 수도 1개(새로 추가 안 됨).
        n = conn.execute(
            "SELECT COUNT(*) c FROM item_code_master WHERE code = ?", (erp_code,)
        ).fetchone()["c"]
    assert n == 1


def test_create_material_force_move_audit_carries_new_material_id():
    """BUG 수정: A6 force 이동의 material_code_cleared audit 이 새 자재 id 를 담는다.

    종전엔 INSERT 가 audit 뒤라 moved_to_material_id=None 이었다. INSERT 를 앞으로
    옮겨 new_id 를 채운다 — 이동 원본→대상 연결을 id 로 추적할 수 있어야 한다.
    """
    import json

    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}9"
    holder_name = f"이전보유{s}"
    with get_connection() as conn:
        holder_id = _seed_material(conn, holder_name, code=code)

    new_name = f"이동대상{s}"
    res = client.post(
        "/api/materials",
        json={"name": new_name, "code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    new_id = res.json()["id"]

    with get_connection() as conn:
        cleared = conn.execute(
            "SELECT details_json FROM audit_logs WHERE action='material_code_cleared' "
            "AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(holder_id),),
        ).fetchone()
    assert cleared is not None
    details = json.loads(cleared["details_json"])
    assert details["moved_to_material_id"] == new_id
    assert details["moved_to_name"] == new_name


def test_delete_material_cleans_orphan_manual_master():
    """A5 삭제로 코드 참조가 사라지면 manual 마스터 유령 행이 정리된다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}A"
    name = f"고아자재{s}"

    created = client.post("/api/materials", json={"name": name, "code": code}, headers=headers)
    assert created.status_code == 200, created.text
    mid = created.json()["id"]
    with get_connection() as conn:
        assert _master_row(conn, code) is not None  # manual 행 생성 확인

    res = client.delete(f"/api/materials/{mid}", headers=headers)
    assert res.status_code == 200, res.text
    with get_connection() as conn:
        assert _master_row(conn, code) is None  # 참조 소멸 → 정리됨


def test_clear_material_code_cleans_manual_master_but_keeps_erp():
    """A3 코드 해제: manual 마스터는 정리, ERP(source!='manual') 마스터는 보존."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    manual_code = f"AS{s}B"
    erp_code = f"AS{s}C"
    manual_name = f"수동{s}"
    erp_name = f"ERP{s}"

    m = client.post(
        "/api/materials", json={"name": manual_name, "code": manual_code}, headers=headers
    )
    assert m.status_code == 200, m.text
    mid_manual = m.json()["id"]

    with get_connection() as conn:
        _seed_master(conn, erp_code, erp_name, "material", source="erp")
        mid_erp = _seed_material(conn, f"{erp_name}자재")
    assign = client.put(
        f"/api/materials/{mid_erp}/code", json={"code": erp_code}, headers=headers
    )
    assert assign.status_code == 200, assign.text

    # 두 코드 모두 해제.
    assert client.put(
        f"/api/materials/{mid_manual}/code", json={"code": None}, headers=headers
    ).status_code == 200
    assert client.put(
        f"/api/materials/{mid_erp}/code", json={"code": None}, headers=headers
    ).status_code == 200

    with get_connection() as conn:
        assert _master_row(conn, manual_code) is None  # manual 정리됨
        erp_row = _master_row(conn, erp_code)
    assert erp_row is not None
    assert erp_row["source"] == "erp"  # ERP 권위 데이터 보존


def test_force_move_keeps_manual_master_since_code_still_used():
    """A3 force 이동은 코드가 새 자재로 옮겨가 여전히 쓰이므로 manual 마스터를 지우지 않는다."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}D"
    holder_name = f"보유{s}"

    created = client.post(
        "/api/materials", json={"name": holder_name, "code": code}, headers=headers
    )
    assert created.status_code == 200, created.text

    with get_connection() as conn:
        target_id = _seed_material(conn, f"이동받는{s}")
    moved = client.put(
        f"/api/materials/{target_id}/code",
        json={"code": code, "force": True},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    with get_connection() as conn:
        assert _master_row(conn, code) is not None  # 코드 여전히 사용 중 → 보존


def test_set_recipe_product_code_inserts_product_master_row():
    """(d) set_recipe_product_code(A4) + 새 product_code → master 행 추가(kind='product')."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    product = f"PMAST{_uid()}"
    code = f"BC{s}1"
    with get_connection() as conn:
        v1, v2 = _build_recipe_chain(conn, product)

    # 체인 중간(v2)에 코드 부여 → 전체 체인 갱신 + master 행 추가.
    res = client.put(
        f"/api/recipes/{v2}/product-code", json={"product_code": code}, headers=headers
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, code)
    assert row is not None
    assert row["kind"] == "product"
    assert row["source"] == "manual"
    assert row["name"] == product


# ---------------- POLISH: force 이동 후 manual 마스터 이름 갱신 ----------------


def test_a3_force_move_refreshes_manual_master_name():
    """POLISH: A3 force 이동 시 manual 마스터 행 이름이 새 보유 자재명으로 갱신된다.

    _ensure_master_entry 는 INSERT OR IGNORE 라 옛 자재명이 고착됐다 — force 이동이면
    manual 행 이름을 새 보유 자재명으로 맞춘다(제안 검색이 엉뚱한 이름 보이던 문제).
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}5"
    holder_name = f"옛보유{s}"
    target_name = f"새보유{s}"
    with get_connection() as conn:
        _seed_material(conn, holder_name, code=code)
        _seed_master(conn, code, holder_name, "material", source="manual", category_hint=None)
        target_id = _seed_material(conn, target_name)  # 코드 없음

    res = client.put(
        f"/api/materials/{target_id}/code",
        json={"code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, code)
    assert row["source"] == "manual"
    assert row["name"] == target_name  # 옛 이름 고착 해소


def test_a6_force_move_refreshes_manual_master_name():
    """POLISH: A6 create_material force 이동 시에도 manual 마스터 이름이 새 자재명으로 갱신."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}6"
    holder_name = f"옛보유{s}"
    new_name = f"신규자재{s}"
    with get_connection() as conn:
        _seed_material(conn, holder_name, code=code)
        _seed_master(conn, code, holder_name, "material", source="manual", category_hint=None)

    res = client.post(
        "/api/materials",
        json={"name": new_name, "code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, code)
    assert row["source"] == "manual"
    assert row["name"] == new_name


def test_force_move_keeps_erp_master_name():
    """POLISH: force 이동이라도 source='erp' 마스터 이름은 갱신하지 않는다(ERP 권위)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}7"
    erp_name = f"ERP명{s}"
    holder_name = f"옛보유{s}"
    target_name = f"새보유{s}"
    with get_connection() as conn:
        _seed_material(conn, holder_name, code=code)
        _seed_master(conn, code, erp_name, "material", source="erp", category_hint="원자재")
        target_id = _seed_material(conn, target_name)

    res = client.put(
        f"/api/materials/{target_id}/code",
        json={"code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        row = _master_row(conn, code)
    assert row["source"] == "erp"
    assert row["name"] == erp_name  # ERP 이름 불변
