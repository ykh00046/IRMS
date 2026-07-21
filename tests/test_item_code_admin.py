"""품목코드 관리 메뉴 — A1~A4 백엔드 엔드포인트 검증.

스펙: scratchpad/item-code-admin-spec.md 의 A(백엔드) 섹션.
기존 테스트(test_recipe_management.py · test_recipe_category.py)의
in-memory 클라이언트/로그인/임포트 패턴을 그대로 따른다.

커버(spec C):
  1. 비로그인/일반 작업자 → A1~A4 전부 401/403.
  2. A1: 마스터 시드 후 q 검색(코드·이름), kind 필터, q 없음 400.
  3. A3: 지정 성공(+audit 행), 해제, 중복 코드 409, 형식 오류 400, 없는 자재 404.
  4. A4: 체인 3개(원본→개정→재개정) 만들고 중간 레시피에 PUT → 3행 갱신,
        다른 체인 중복 409, 해제 시 체인 전체 NULL.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    """각 테스트 종료 후 이 모듈이 심은 item_code_master 행을 삭제.

    테스트 DB(.tmp-tests/pytest-data/irms.db)는 실행 간 유지되므로, 마스터 행을
    남기면 import_parser 의 마스터 존재 판정이 바뀌어 다른 테스트(미리보기 자동
    등록 경고 등)가 회귀한다. 다음 두 source 의 행을 지운다:
      - 'test_item_code_admin': _seed_master_row 로 직접 심은 ERP 시뮬레이션 행.
      - 'manual': A3(set_material_code)·A4(set_recipe_product_code) 가
        _ensure_master_entry 를 경유해 새 코드를 부여할 때 자동으로 들어가는 행.
    """
    yield
    _cleanup_test_master_rows()


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
    """짧은 고유 접미사(품목코드용 — 'AS'+5자 = 7자, 여유 있게 digit 붙여도 10자 이내)."""
    return uuid.uuid4().hex[:n].upper()


def _import(client, headers, product, a, b, revision_of=None):
    """레시피 1건 등록(등록 즉시 completed) → id 반환."""
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    if revision_of is not None:
        body["revision_of"] = revision_of
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def _seed_material(conn, name, code=None):
    """materials 행 직접 삽입 → id 반환(기존 테스트의 INSERT 패턴)."""
    cur = conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code) "
        "VALUES (?, 'weight', 'g', 'none', NULL, 1, ?)",
        (name, code),
    )
    conn.commit()
    return cur.lastrowid


def _seed_master_row(conn, code, name, kind, category_hint=None):
    """item_code_master 행 직접 삽입(ERP 임포트 시뮬레이션).

    source 컬럼에 'test_item_code_admin' 을 표시해 두어, 각 테스트 종료 후
    _cleanup_test_master_rows 가 이 테스트가 만든 행만 지울 수 있게 한다.
    (테스트 DB 가 실행 간 유지되므로, 마스터 행을 남기면 import_parser 의
     마스터 존재 여부 판정이 바뀌어 다른 테스트(test_route_coverage 의 미리보기
     자동 등록 경고 등)가 회귀한다.)
    """
    conn.execute(
        "INSERT INTO item_code_master (code, name, kind, category_hint, source, imported_at) "
        "VALUES (?, ?, ?, ?, 'test_item_code_admin', '2026-07-01T00:00:00Z')",
        (code, name, kind, category_hint),
    )
    conn.commit()


def _cleanup_test_master_rows():
    """이 모듈이 남긴 item_code_master 행 삭제 — DB 오염 방지.

    직접 심은 행(source='test_item_code_admin')과, A3/A4 PUT 이
    _ensure_master_entry 로 자동 생성한 행(source='manual')을 함께 지운다.
    manual 행을 남기면 test_route_coverage 의 미리보기 자동 등록 정책 판정이
    바뀌어 회귀한다.
    """
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "DELETE FROM item_code_master "
            "WHERE source IN ('test_item_code_admin', 'manual')"
        )
        conn.commit()


# ---------------- 1. 권한: 비책임자/미로그인 차단 ----------------


def test_non_manager_endpoints_blocked():
    """비로그인(또는 비책임자) → A1~A4 전부 401/403."""
    client = _client()
    # 로그인 없이 전 엔드포인트 호출 → 전부 차단
    a1 = client.get("/api/item-codes/master", params={"q": "AS"})
    assert a1.status_code in (401, 403)

    a2 = client.get("/api/item-codes/materials")
    assert a2.status_code in (401, 403)

    a3 = client.put("/api/materials/1/code", json={"code": "AS0001"})
    assert a3.status_code in (401, 403)

    a4 = client.put("/api/recipes/1/product-code", json={"product_code": "PA0001"})
    assert a4.status_code in (401, 403)


# ---------------- 2. A1: 마스터 검색 ----------------


def test_master_search_by_code_and_name():
    """q 로 코드 검색·이름 검색 모두 동작, kind 필터, q 없음 400."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    mat_code = f"AS{s}1"   # 영문2 + 6자 (총 8자, digit 붙여도 안전)
    mat_code2 = f"AS{s}2"
    prod_code = f"BC{s}1"
    mat_name = f"HEMA{s}"
    mat_name2 = f"DMA{s}"
    prod_name = f"SOUL{s}"

    with get_connection() as conn:
        _seed_master_row(conn, mat_code, mat_name, "material", "원자재")
        _seed_master_row(conn, mat_code2, mat_name2, "material", "원자재")
        _seed_master_row(conn, prod_code, prod_name, "product", "합성")

    # 이름 검색(이름 접두사로 고유 보장)
    res = client.get(
        "/api/item-codes/master", params={"q": f"HEMA{s}"}, headers=headers
    )
    assert res.status_code == 200, res.text
    codes = [it["code"] for it in res.json()["items"]]
    assert mat_code in codes

    # 코드 검색(대소문자 무시 — 소문자로 검색)
    res = client.get(
        "/api/item-codes/master", params={"q": mat_code2.lower()}, headers=headers
    )
    assert res.status_code == 200, res.text
    codes = [it["code"] for it in res.json()["items"]]
    assert mat_code2 in codes

    # kind 필터 — product 만
    res = client.get(
        "/api/item-codes/master", params={"q": prod_name, "kind": "product"}, headers=headers
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [it["code"] for it in items] == [prod_code]

    # kind=material 로 검색하면 product 행은 빠진다
    res = client.get(
        "/api/item-codes/master", params={"q": prod_name, "kind": "material"}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


def test_master_search_missing_q_is_400():
    """q 없음(또는 공백) → 400."""
    client = _client()
    headers = _login(client)

    res = client.get("/api/item-codes/master", headers=headers)
    assert res.status_code == 400

    res = client.get("/api/item-codes/master", params={"q": "   "}, headers=headers)
    assert res.status_code == 400


def test_master_search_invalid_kind_is_400():
    """kind 가 허용값이 아니면 400."""
    client = _client()
    headers = _login(client)

    res = client.get(
        "/api/item-codes/master", params={"q": "x", "kind": "other"}, headers=headers
    )
    assert res.status_code == 400


# ---------------- 3. A3: 자재 코드 지정/해제 ----------------


def test_material_set_code_success_and_audit():
    """지정 성공 → 200, master_name 참고 노출, audit 행 존재."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}9"
    master_name = f"HEMA{s}"
    with get_connection() as conn:
        mid = _seed_material(conn, f"자재{s}")
        _seed_master_row(conn, code, master_name, "material")

    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["material_id"] == mid
    assert body["code"] == code
    assert body["master_name"] == master_name

    # audit 행 존재
    with get_connection() as conn:
        row = conn.execute(
            "SELECT action, details_json FROM audit_logs "
            "WHERE action='material_code_set' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(mid),),
        ).fetchone()
    assert row is not None
    assert code in (row["details_json"] or "")


def test_material_set_code_lowercase_normalized_to_upper():
    """소문자 코드 → 대문자로 정규화 저장."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"as{s}4"
    with get_connection() as conn:
        mid = _seed_material(conn, f"자재{s}")

    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["code"] == code.upper()


def test_material_clear_code():
    """code=null → 해제(NULL 저장)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}1"
    with get_connection() as conn:
        mid = _seed_material(conn, f"자재{s}", code=code)

    res = client.put(f"/api/materials/{mid}/code", json={"code": None}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["code"] is None

    with get_connection() as conn:
        stored = conn.execute(
            "SELECT code FROM materials WHERE id=?", (mid,)
        ).fetchone()["code"]
    assert stored is None


def test_material_clear_code_with_empty_string():
    """code='' → 해제(빈 문자열도 해제로 취급)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}2"
    with get_connection() as conn:
        mid = _seed_material(conn, f"자재{s}", code=code)

    res = client.put(f"/api/materials/{mid}/code", json={"code": ""}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["code"] is None


def test_material_duplicate_code_409():
    """다른 자재가 같은 code 를 쓰면 409(자재명 포함)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}5"
    other_name = f"기존자재{s}"
    with get_connection() as conn:
        _seed_material(conn, other_name, code=code)
        mid = _seed_material(conn, f"새자재{s}")

    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 409
    assert other_name in res.json()["detail"]


def test_material_same_code_same_material_idempotent():
    """같은 자재가 자기 code 를 다시 지정 → 자기 자신은 충돌 아님(200)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}7"
    with get_connection() as conn:
        mid = _seed_material(conn, f"자재{s}", code=code)

    res = client.put(f"/api/materials/{mid}/code", json={"code": code}, headers=headers)
    assert res.status_code == 200


def test_material_invalid_code_format_400():
    """형식(영문 1~2자 + 영문/숫자 2~8자) 위반 → 400.

    BUG 3 으로 자재 코드 패턴이 영문 1~2자 접두로 완화되어, AS1(총 3자) 은 이제
    유효(B-단일 접두 자재 코드 지원)하다. 따라서 형식 가드 테스트는 새 규칙에서도
    명백히 위반되는 값(숫자만 / 한글 포함 / 영문 접두 없는 2자)만 쓴다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    with get_connection() as conn:
        mid = _seed_material(conn, f"자재{_uid()}")

    # 숫자로만 — 영문 접두 없음
    res = client.put(f"/api/materials/{mid}/code", json={"code": "1234"}, headers=headers)
    assert res.status_code == 400

    # 한글 포함
    res = client.put(f"/api/materials/{mid}/code", json={"code": "AS한글"}, headers=headers)
    assert res.status_code == 400

    # 영문 접두 없는 2자 숫자/기호 혼합 — 접두 영문 1~2자 요건 위반
    res = client.put(f"/api/materials/{mid}/code", json={"code": "12"}, headers=headers)
    assert res.status_code == 400


def test_material_not_found_404():
    """없는 자재 → 404."""
    client = _client()
    headers = _login(client)

    res = client.put("/api/materials/9999999/code", json={"code": "AS0001"}, headers=headers)
    assert res.status_code == 404


def test_material_list_filters():
    """A2: 자재 목록 — uncoded/q 필터, is_active=1 만."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    suffix = _uid()
    coded_code = f"AS{suffix}"[:10]
    with get_connection() as conn:
        coded = _seed_material(conn, f"검색자재{suffix}", code=coded_code)
        uncoded_id = _seed_material(conn, f"검색자재빈{suffix}", code=None)
        # 비활성 자재는 노출되지 않아야
        conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, is_active) "
            "VALUES (?, 'weight', 'g', 'none', 0)",
            (f"비활성{suffix}",),
        )
        conn.commit()

    # 전체(검색어로 한정)
    res = client.get(
        "/api/item-codes/materials", params={"q": suffix}, headers=headers
    )
    assert res.status_code == 200, res.text
    names = [it["name"] for it in res.json()["items"]]
    assert f"검색자재{suffix}" in names
    assert f"검색자재빈{suffix}" in names
    assert f"비활성{suffix}" not in names  # is_active=0 제외

    # uncoded=1 → code 가 없는 자재만
    res = client.get(
        "/api/item-codes/materials", params={"q": suffix, "uncoded": "1"}, headers=headers
    )
    assert res.status_code == 200
    ids = [it["id"] for it in res.json()["items"]]
    assert uncoded_id in ids
    assert coded not in ids


# ---------------- 4. A4: 반제품 코드(체인 전체) ----------------


def _build_chain(conn, product):
    """원본→개정→재개정 3단 체인 직접 INSERT → (v1, v2, v3) id."""
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
    v3 = conn.execute(
        "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, revision_of) "
        "VALUES (?, ?, 'completed', 't', '2026-07-03T00:00:00Z', ?)",
        (product, product, v2),
    ).lastrowid
    conn.commit()
    return v1, v2, v3


def test_product_code_chain_update_from_middle():
    """3단 체인의 중간 레시피에 PUT → 3행 모두 갱신."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PCHAIN{_uid()}"
    code = f"BC{_short()}1"
    with get_connection() as conn:
        v1, v2, v3 = _build_chain(conn, product)

    # 중간(v2) 에 코드 지정 → 체인 전체(v1,v2,v3) 갱신
    res = client.put(
        f"/api/recipes/{v2}/product-code", json={"product_code": code}, headers=headers
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["product_code"] == code
    assert body["updated"] == 3

    with get_connection() as conn:
        rows = {
            r["id"]: r["product_code"]
            for r in conn.execute(
                "SELECT id, product_code FROM recipes WHERE id IN (?, ?, ?)", (v1, v2, v3)
            ).fetchall()
        }
    assert rows[v1] == code
    assert rows[v2] == code
    assert rows[v3] == code


def test_product_code_set_from_root_updates_chain():
    """루트(v1) 에서 지정해도 전체 체인 갱신(updated==3)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PROOT{_uid()}"
    code = f"BC{_short()}2"
    with get_connection() as conn:
        v1, v2, v3 = _build_chain(conn, product)

    res = client.put(
        f"/api/recipes/{v1}/product-code", json={"product_code": code}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 3

    with get_connection() as conn:
        rows = {
            r["id"]: r["product_code"]
            for r in conn.execute(
                "SELECT id, product_code FROM recipes WHERE id IN (?, ?, ?)", (v1, v2, v3)
            ).fetchall()
        }
    assert all(c == code for c in rows.values())


def test_product_code_clear_propagates_to_whole_chain():
    """해제(null) → 체인 전체 NULL."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PCLEAR{_uid()}"
    code = f"BC{_short()}3"
    with get_connection() as conn:
        v1, v2, v3 = _build_chain(conn, product)
        # 먼저 코드 부여
        placeholders = ",".join("?" for _ in (v1, v2, v3))
        conn.execute(
            f"UPDATE recipes SET product_code=? WHERE id IN ({placeholders})",
            [code, v1, v2, v3],
        )
        conn.commit()

    res = client.put(
        f"/api/recipes/{v3}/product-code", json={"product_code": None}, headers=headers
    )
    assert res.status_code == 200, res.text

    with get_connection() as conn:
        rows = {
            r["id"]: r["product_code"]
            for r in conn.execute(
                "SELECT id, product_code FROM recipes WHERE id IN (?, ?, ?)", (v1, v2, v3)
            ).fetchall()
        }
    assert rows[v1] is None
    assert rows[v2] is None
    assert rows[v3] is None


def test_product_code_duplicate_in_other_chain_409():
    """다른 체인의 레시피가 같은 product_code 사용 중 → 409(반제품명 포함)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product_a = f"PDUPA{_uid()}"
    product_b = f"PDUPB{_uid()}"
    code = f"BC{_short()}9"
    with get_connection() as conn:
        a1, a2, a3 = _build_chain(conn, product_a)
        b1, b2, b3 = _build_chain(conn, product_b)
        # B 체인에 코드 선점
        placeholders = ",".join("?" for _ in (b1, b2, b3))
        conn.execute(
            f"UPDATE recipes SET product_code=? WHERE id IN ({placeholders})",
            [code, b1, b2, b3],
        )
        conn.commit()

    # A 체인에 같은 코드 지정 시도 → 409
    res = client.put(
        f"/api/recipes/{a2}/product-code", json={"product_code": code}, headers=headers
    )
    assert res.status_code == 409
    assert product_b in res.json()["detail"]


def test_product_code_same_chain_no_conflict():
    """같은 체인 내 재지정(이미 같은 코드) → 자기 체인은 충돌 아님(200)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PSAME{_uid()}"
    code = f"BC{_short()}4"
    with get_connection() as conn:
        v1, v2, v3 = _build_chain(conn, product)
        placeholders = ",".join("?" for _ in (v1, v2, v3))
        conn.execute(
            f"UPDATE recipes SET product_code=? WHERE id IN ({placeholders})",
            [code, v1, v2, v3],
        )
        conn.commit()

    res = client.put(
        f"/api/recipes/{v2}/product-code", json={"product_code": code}, headers=headers
    )
    assert res.status_code == 200, res.text


def test_product_code_invalid_format_400():
    """형식 위반 → 400."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PINVAL{_uid()}"
    with get_connection() as conn:
        v1, _, _ = _build_chain(conn, product)

    res = client.put(
        f"/api/recipes/{v1}/product-code", json={"product_code": "1234"}, headers=headers
    )
    assert res.status_code == 400


def test_product_code_recipe_not_found_404():
    """없는 레시피 → 404."""
    client = _client()
    headers = _login(client)

    res = client.put(
        "/api/recipes/9999999/product-code",
        json={"product_code": "BC0001"},
        headers=headers,
    )
    assert res.status_code == 404


def test_product_code_set_writes_audit():
    """코드 지정 시 audit 행(recipe_product_code_set) 기록 — details 에 code·updated."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PAUD{_uid()}"
    code = f"BC{_short()}7"
    with get_connection() as conn:
        v1, v2, v3 = _build_chain(conn, product)

    res = client.put(
        f"/api/recipes/{v1}/product-code", json={"product_code": code}, headers=headers
    )
    assert res.status_code == 200, res.text
    updated = res.json()["updated"]

    with get_connection() as conn:
        row = conn.execute(
            "SELECT action, details_json FROM audit_logs "
            "WHERE action='recipe_product_code_set' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(v1),),
        ).fetchone()
    assert row is not None
    details = row["details_json"] or ""
    assert code in details
    assert str(updated) in details


# ---------------- 5. A5: 자재 삭제(DELETE /materials/{id}) ----------------


def test_material_delete_non_manager_blocked():
    """비책임자(또는 비로그인) DELETE → 401/403."""
    client = _client()
    res = client.delete("/api/materials/1")
    assert res.status_code in (401, 403)


def test_material_delete_no_references_succeeds():
    """참조 없는 자재 삭제 → 200 + 행 삭제 + blend_details 링크 NULL + audit 행."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}3"
    name = f"오등록자재{s}"
    with get_connection() as conn:
        mid = _seed_material(conn, name, code=code)
        # blend_records 부모행 1건 — blend_details.blend_record_id NOT NULL.
        conn.execute(
            "INSERT INTO blend_records "
            "(product_lot, product_name, worker, work_date, total_amount, status, created_at) "
            "VALUES (?, ?, 't', '2026-07-01', 100, 'completed', '2026-07-01T00:00:00Z')",
            (f"PL{s}", name),
        )
        brid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # blend_details 링크 2건 — 기록의 텍스트는 보존하되 material_id 만 NULL.
        conn.execute(
            "INSERT INTO blend_details "
            "(blend_record_id, material_id, material_name, material_code, ratio, "
            " theory_amount, actual_amount, sequence_order, created_at) "
            "VALUES (?, ?, ?, ?, 0.5, 100, 99, 0, '2026-07-01T00:00:00Z')",
            (brid, mid, name, code),
        )
        conn.execute(
            "INSERT INTO blend_details "
            "(blend_record_id, material_id, material_name, material_code, ratio, "
            " theory_amount, actual_amount, sequence_order, created_at) "
            "VALUES (?, ?, ?, ?, 0.5, 100, 99, 1, '2026-07-01T00:00:00Z')",
            (brid, mid, name, code),
        )
        # material_aliases 도 1건(FK CASCADE 로 자동 삭제 예상).
        conn.execute(
            "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, ?)",
            (mid, f"별칭{s}"),
        )
        conn.commit()

    res = client.delete(f"/api/materials/{mid}", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ok"
    assert body["deleted"] == name

    with get_connection() as conn:
        # 행이 삭제됨.
        exists = conn.execute(
            "SELECT id FROM materials WHERE id = ?", (mid,)
        ).fetchone()
        assert exists is None
        # blend_details 링크만 NULL — 이름/코드 텍스트 보존.
        links = conn.execute(
            "SELECT material_id, material_name, material_code FROM blend_details "
            "WHERE material_name = ?",
            (name,),
        ).fetchall()
        assert len(links) == 2
        for row in links:
            assert row["material_id"] is None
            assert row["material_name"] == name
            assert row["material_code"] == code
        # aliases 도 CASCADE 로 정리.
        aliases = conn.execute(
            "SELECT id FROM material_aliases WHERE material_id = ?", (mid,)
        ).fetchall()
        assert len(aliases) == 0
        # audit 행 — material_deleted, target_id=str(mid), details 에 code·links 포함.
        arow = conn.execute(
            "SELECT action, target_type, target_label, details_json FROM audit_logs "
            "WHERE action='material_deleted' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(mid),),
        ).fetchone()
    assert arow is not None
    assert arow["target_type"] == "material"
    assert arow["target_label"] == name
    details_json = arow["details_json"] or ""
    assert code in details_json
    assert "blend_detail_links" in details_json


def test_material_delete_referenced_by_recipe_409():
    """레시피가 참조하는 자재 → 409 + detail 에 반제품명 + 자재 잔존."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    name = f"참조자재{s}"
    product_name = f"PB{s}"
    with get_connection() as conn:
        mid = _seed_material(conn, name)
        # recipe + recipe_items 한 건 — material_id 참조.
        rid = conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
            "VALUES (?, ?, 'completed', 't', '2026-07-01T00:00:00Z')",
            (product_name, product_name),
        ).lastrowid
        conn.execute(
            "INSERT INTO recipe_items (recipe_id, material_id, value_weight) VALUES (?, ?, 10)",
            (rid, mid),
        )
        conn.commit()

    res = client.delete(f"/api/materials/{mid}", headers=headers)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert product_name in detail
    assert "레시피" in detail

    with get_connection() as conn:
        # 자재는 잔존(삭제 아님, 비활성화도 아님).
        row = conn.execute(
            "SELECT id, is_active FROM materials WHERE id = ?", (mid,)
        ).fetchone()
    assert row is not None
    assert row["is_active"] == 1


def test_material_delete_not_found_404():
    """없는 자재 → 404."""
    client = _client()
    headers = _login(client)

    res = client.delete("/api/materials/9999999", headers=headers)
    assert res.status_code == 404


# ---------------- 6. A3 force=true — 코드 이동 지정 ----------------


def test_material_set_code_force_moves_code():
    """(a) PUT force=true → 코드를 이전 보유 자재에서 빼고 대상에 부여(이동).

    대상 자재가 코드를 갖고, 이전 보유 자재의 code 는 NULL. 응답 moved_from 은 이전
    보유 자재명. audit: material_code_cleared(이전 보유) + material_code_set(대상).
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}1"
    holder_name = f"이전보유{s}"
    target_name = f"이동대상{s}"
    with get_connection() as conn:
        holder_id = _seed_material(conn, holder_name, code=code)
        target_id = _seed_material(conn, target_name)

    res = client.put(
        f"/api/materials/{target_id}/code",
        json={"code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == code
    assert body["moved_from"] == holder_name

    with get_connection() as conn:
        # 대상이 코드 보유, 이전 보유는 NULL.
        assert conn.execute(
            "SELECT code FROM materials WHERE id = ?", (target_id,)
        ).fetchone()["code"] == code
        assert conn.execute(
            "SELECT code FROM materials WHERE id = ?", (holder_id,)
        ).fetchone()["code"] is None
        # audit: material_code_cleared(이전 보유).
        cleared = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action='material_code_cleared' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(holder_id),),
        ).fetchone()
        assert cleared is not None
        assert code in (cleared["details_json"] or "")
        assert str(target_id) in (cleared["details_json"] or "")
        # audit: material_code_set(대상) — moved_from_name 포함.
        setrow = conn.execute(
            "SELECT details_json FROM audit_logs "
            "WHERE action='material_code_set' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(target_id),),
        ).fetchone()
        assert setrow is not None
        assert holder_name in (setrow["details_json"] or "")


def test_material_set_code_force_moves_code_from_inactive_holder():
    """(b) PUT force=true — 비활성(is_active=0) 보유 자재에서도 코드 이동.

    비활성 자재는 GET /item-codes/materials(is_active=1 필터) 에 안 보여 빠져나가지
    못하지만, force 이동은 is_active 와 무관해야 한다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}2"
    holder_name = f"비활성보유{s}"
    target_name = f"이동대상{s}"
    with get_connection() as conn:
        # 비활성 보유 자재 — is_active=0 으로 직접 INSERT.
        holder_id = conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, is_active, code) "
            "VALUES (?, 'weight', 'g', 'none', 0, ?)",
            (holder_name, code),
        ).lastrowid
        target_id = _seed_material(conn, target_name)
        conn.commit()

    res = client.put(
        f"/api/materials/{target_id}/code",
        json={"code": code, "force": True},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == code
    assert body["moved_from"] == holder_name

    with get_connection() as conn:
        assert conn.execute(
            "SELECT code FROM materials WHERE id = ?", (target_id,)
        ).fetchone()["code"] == code
        assert conn.execute(
            "SELECT code FROM materials WHERE id = ?", (holder_id,)
        ).fetchone()["code"] is None


def test_material_set_code_without_force_still_409():
    """(c) PUT force 없이 코드 충돌 → 종전대로 409(회귀 가드)."""
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    code = f"AS{s}3"
    holder_name = f"보유자재{s}"
    with get_connection() as conn:
        _seed_material(conn, holder_name, code=code)
        target_id = _seed_material(conn, f"대상{s}")

    # force 생략 → 409
    res = client.put(
        f"/api/materials/{target_id}/code", json={"code": code}, headers=headers
    )
    assert res.status_code == 409
    assert holder_name in res.json()["detail"]

    # 명시적으로 force=false → 여전히 409
    res = client.put(
        f"/api/materials/{target_id}/code",
        json={"code": code, "force": False},
        headers=headers
    )
    assert res.status_code == 409


# ---------------- 7. BUG 2/3 — 코드 형식 완화(B-단일 접두 허용) ----------------


def test_recipe_product_code_accepts_single_letter_prefix():
    """(3) BUG 2: PUT /recipes/{id}/product-code 가 B0082(영문 1자 접두)를 수용.

    종전에는 _validate_code(영문 2자) 를 써 B0082/BC/BW 가 400 로 거절됐다.
    _validate_product_code(영문 1~2자) 도입 후 현황 인라인 지정이 동작해야 한다.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    product = f"PB{_uid()}"
    with get_connection() as conn:
        v1, v2, v3 = _build_chain(conn, product)

    # B0082 형태(영문 1자 접두) — 종전 400, 수정 후 200.
    code = f"B{_short(4)}"
    res = client.put(
        f"/api/recipes/{v1}/product-code",
        json={"product_code": code},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["product_code"] == code


def test_material_code_accepts_single_letter_prefix():
    """(4) BUG 3: PUT /materials/{id}/code 가 B0020(반제품계 자재 코드)을 수용.

    반제품(PB/B0020)이 자재로 전용되어 B-단일 접두 코드를 가질 때 UI 재지정이
    막히지 않도록 _validate_code 를 영문 1~2자 패턴으로 완화.
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    s = _short()
    with get_connection() as conn:
        mid = _seed_material(conn, f"반제품자재{s}")

    code = f"B{s}020"[:7]  # B + 영숫자 — 영문 1자 접두
    res = client.put(
        f"/api/materials/{mid}/code", json={"code": code}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["code"] == code


def test_material_code_format_guard_still_rejects_a1():
    """(5) 형식 가드 유지 — 너무 짧은 코드(예: A1)는 여전히 400.

    접두 완화가 무분별한 값을 허용하지 않음을 확인(영문 1~2자 + 영숫자 2~8자).
    """
    client = _client()
    headers = _login(client)

    from src.db import get_connection

    with get_connection() as conn:
        mid = _seed_material(conn, f"가드자재{_uid()}")

    res = client.put(
        f"/api/materials/{mid}/code", json={"code": "A1"}, headers=headers
    )
    assert res.status_code == 400
