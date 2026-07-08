"""레시피 관리 개선 — DHR 승계·체인 지정·상태 단순화·현재버전 배합노출.

검토 후속 수정(2026-07-03):
  #1 수정 등록 시 원본 체인의 is_dhr 승계
  #2 DHR 지정/해제를 버전 체인 전체에 적용
  #3 GET /recipes 가 is_dhr 반환
  #4 등록 즉시 completed + pending/in_progress → completed 마이그레이션 + 취소 허용
  #5c 배합 레시피 목록은 현재 버전(tip)만

테스트 DB(.tmp-tests)는 실행 간 유지되므로 중복 등록(DUPLICATE_IMPORT)을 피하기 위해
제품명에 실행마다 유니크한 토큰을 붙인다.
"""

from __future__ import annotations

import importlib
import uuid


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


def _import(client, headers, product, a, b, revision_of=None):
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    if revision_of is not None:
        body["revision_of"] = revision_of
    res = client.post("/api/recipes/import", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["created_ids"][0]


def test_import_registers_completed_and_usable_in_blend():
    """등록 즉시 사용(completed) — pending 정체 없이 배합 화면에 바로 노출."""
    client = _client()
    headers = _login(client)
    product = f"PALPHA{_uid()}"
    rid = _import(client, headers, product, 60, 40)

    from src.db import get_connection

    with get_connection() as conn:
        status = conn.execute("SELECT status FROM recipes WHERE id = ?", (rid,)).fetchone()["status"]
    assert status == "completed"

    names = [r["product_name"] for r in client.get("/api/blend/recipes").json()["items"]]
    assert product in names


def test_revision_inherits_dhr_flag():
    """수정 등록 시 원본이 DHR 전용이면 새 버전도 DHR 전용을 승계 — 배합 노출 회귀 방지."""
    client = _client()
    headers = _login(client)
    product = f"PBETA{_uid()}"
    base_id = _import(client, headers, product, 60, 40)

    # 원본을 DHR 전용으로 지정
    res = client.patch(f"/api/recipes/{base_id}/dhr", json={"is_dhr": True}, headers=headers)
    assert res.status_code == 200

    # 수정 등록(revision) → is_dhr 승계되어야 함
    rev_id = _import(client, headers, product, 70, 30, revision_of=base_id)

    from src.db import get_connection

    with get_connection() as conn:
        is_dhr = conn.execute("SELECT is_dhr FROM recipes WHERE id = ?", (rev_id,)).fetchone()["is_dhr"]
    assert is_dhr == 1

    # 일반 배합 목록에는 노출되지 않아야 함
    names = [r["product_name"] for r in client.get("/api/blend/recipes").json()["items"]]
    assert product not in names


def test_dhr_set_applies_to_whole_chain():
    """DHR 지정을 한 버전에 하면 revision 체인 전체가 함께 지정/해제된다."""
    client = _client()
    headers = _login(client)
    product = f"PGAMMA{_uid()}"

    from src.db import get_connection

    with get_connection() as conn:
        base = conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
            "VALUES (?, ?, 'completed', 't', '2026-07-01T00:00:00Z')",
            (product, product),
        ).lastrowid
        rev = conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, revision_of) "
            "VALUES (?, ?, 'completed', 't', '2026-07-02T00:00:00Z', ?)",
            (product, product, base),
        ).lastrowid
        conn.commit()

    # 옛 버전(base) 하나에만 지정 → 체인 전체 반영
    res = client.patch(f"/api/recipes/{base}/dhr", json={"is_dhr": True}, headers=headers)
    assert res.status_code == 200
    assert res.json()["chain_count"] == 2

    with get_connection() as conn:
        flags = {
            r["id"]: r["is_dhr"]
            for r in conn.execute(
                "SELECT id, is_dhr FROM recipes WHERE id IN (?, ?)", (base, rev)
            ).fetchall()
        }
    assert flags[base] == 1 and flags[rev] == 1


def test_list_recipes_shows_only_latest_revision():
    """레시피 현황(GET /recipes)은 개정 체인의 최신 버전만 — 수정 등록해도 같은
    제품이 줄줄이 늘어나 보이지 않는다. 개정본이 취소되면 원본이 복귀."""
    client = _client()
    headers = _login(client)
    product = f"PTIP{_uid()}"
    v1 = _import(client, headers, product, 60, 40)
    v2 = _import(client, headers, product, 70, 30, revision_of=v1)

    def visible_ids():
        items = client.get("/api/recipes", params={"search": product}).json()["items"]
        return [r["id"] for r in items]

    ids = visible_ids()
    assert v2 in ids and v1 not in ids  # 최신만 한 줄

    # 개정본 취소 → 원본이 현황에 복귀
    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE recipes SET status = 'canceled' WHERE id = ?", (v2,))
        conn.commit()
    ids = visible_ids()
    assert v1 in ids


def test_material_order_preserved_through_revision():
    """자재 순서(=투입 순서)는 등록 순서 그대로 — 상세/TSV/개정을 거쳐도 불변.

    fetch_recipe_items 가 이름순(m.name) 정렬이라 '수정 등록'이 가나다순으로
    재배열된 시트를 저장 → 개정 때마다 배합 순서가 뒤바뀌던 회귀(2026-07-08)."""
    client = _client()
    headers = _login(client)
    product = f"PORD{_uid()}"
    # 의도적으로 가나다/알파벳 역순 자재 배치
    raw = f"반제품명\t원료Z\t원료M\t원료A\n{product}\t50\t30\t20"
    res = client.post("/api/recipes/import", json={"raw_text": raw, "force": True}, headers=headers)
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]

    def names(recipe_id):
        detail = client.get(f"/api/recipes/{recipe_id}/detail").json()
        return [it["material_name"] for it in detail["items"]]

    assert names(rid) == ["원료Z", "원료M", "원료A"]  # 상세: 등록 순서

    # 상세 TSV(수정 등록이 불러오는 형식)도 등록 순서
    tsv_header = client.get(f"/api/recipes/{rid}/detail").json()["tsv"].split("\n")[0]
    assert tsv_header.split("\t")[1:4] == ["원료Z", "원료M", "원료A"]

    # 개정(값 변경) 후에도 순서 유지
    raw2 = f"반제품명\t원료Z\t원료M\t원료A\n{product}\t60\t25\t15"
    res2 = client.post("/api/recipes/import",
                       json={"raw_text": raw2, "force": True, "revision_of": rid},
                       headers=headers)
    assert res2.status_code == 200, res2.text
    rid2 = res2.json()["created_ids"][0]
    assert names(rid2) == ["원료Z", "원료M", "원료A"]

    # 배합 화면 환산 API 도 동일 순서
    blend = client.get(f"/api/blend/recipes/{rid2}").json()
    assert [it["material_name"] for it in blend["items"]] == ["원료Z", "원료M", "원료A"]


def test_version_history_current_flag():
    """버전 이력: 현재 버전만 is_current — 개정본이 취소되면 원본이 현재로 복귀."""
    client = _client()
    headers = _login(client)
    product = f"PCUR{_uid()}"
    v1 = _import(client, headers, product, 60, 40)
    v2 = _import(client, headers, product, 70, 30, revision_of=v1)

    def current_map():
        items = client.get(f"/api/recipes/{v1}/history").json()["items"]
        return {it["id"]: it["is_current"] for it in items}

    m = current_map()
    assert m[v2] is True and m[v1] is False  # v2 만 현재

    from src.db import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE recipes SET status = 'canceled' WHERE id = ?", (v2,))
        conn.commit()
    m = current_map()
    assert m[v1] is True and m[v2] is False  # 취소 → v1 복귀


def test_list_recipes_returns_is_dhr():
    """GET /recipes 응답에 is_dhr 포함 — 현황 표 DHR 칩 표시 근거."""
    client = _client()
    headers = _login(client)
    product = f"PDELTA{_uid()}"
    rid = _import(client, headers, product, 50, 50)
    client.patch(f"/api/recipes/{rid}/dhr", json={"is_dhr": True}, headers=headers)

    items = client.get("/api/recipes").json()["items"]
    target = next(it for it in items if it["id"] == rid)
    assert target["is_dhr"] == 1


def test_cancel_allowed_from_completed():
    """등록 즉시 completed 이므로, 취소는 completed 상태에서도 허용되어야 한다."""
    client = _client()
    headers = _login(client)
    product = f"PEPSILON{_uid()}"
    rid = _import(client, headers, product, 30, 70)

    res = client.patch(f"/api/recipes/{rid}/status", json={"action": "cancel"}, headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "canceled"


def test_status_migration_converts_pending_to_completed():
    """마이그레이션: 정체돼 있던 pending/in_progress 레시피를 completed 로 전환(취소 보존)."""
    client = _client()
    _login(client)
    product = f"PZETA{_uid()}"

    from src.db import get_connection
    from src.db.migrations import apply_schema_migrations

    with get_connection() as conn:
        pend = conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
            "VALUES (?, ?, 'pending', 't', '2026-06-01T00:00:00Z')",
            (product, product),
        ).lastrowid
        canc = conn.execute(
            "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at) "
            "VALUES (?, ?, 'canceled', 't', '2026-06-01T00:00:00Z')",
            (product + "C", product + "C"),
        ).lastrowid
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = 'recipes_status_active_default'"
        )
        apply_schema_migrations(conn)
        conn.commit()
        rows = {
            r["id"]: r["status"]
            for r in conn.execute(
                "SELECT id, status FROM recipes WHERE id IN (?, ?)", (pend, canc)
            ).fetchall()
        }
    assert rows[pend] == "completed"
    assert rows[canc] == "canceled"  # 취소는 보존
