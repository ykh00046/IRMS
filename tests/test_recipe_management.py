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


def test_mid_chain_cancel_keeps_single_tip():
    """감사 F-4: 3세대 체인(v1→v2→v3)에서 중간 v2 만 취소해도 현재 버전은 v3 하나.

    옛 규칙(직계 자식만 확인)은 v1(자식 v2가 취소라 안 숨겨짐)과 v3(자식 없음)를
    동시에 노출하고, 배합 귀결은 v1 에 머물러 **옛 배합비로 이론량이 산출**됐다.
    목록 2곳(현황/배합)과 배합 귀결이 모두 v3 로 수렴해야 한다.
    """
    from src.db import get_connection
    from src.services.blend_service import _resolve_latest_revision

    client = _client()
    headers = _login(client)
    product = f"PMID{_uid()}"
    v1 = _import(client, headers, product, 60, 40)
    v2 = _import(client, headers, product, 70, 30, revision_of=v1)
    v3 = _import(client, headers, product, 80, 20, revision_of=v2)

    with get_connection() as conn:  # 중간 세대만 취소 (오등록 정정)
        conn.execute("UPDATE recipes SET status = 'canceled' WHERE id = ?", (v2,))
        conn.commit()

    status_ids = [r["id"] for r in client.get(
        "/api/recipes", params={"search": product}).json()["items"]]
    assert v3 in status_ids
    assert v1 not in status_ids and v2 not in status_ids  # 두 줄로 갈라지지 않는다

    blend_ids = [r["id"] for r in client.get("/api/blend/recipes").json()["items"]
                 if r["product_name"] == product]
    assert blend_ids == [v3]

    with get_connection() as conn:  # 옛 id 로 들어와도 최신본으로 귀결
        assert _resolve_latest_revision(conn, v1) == v3
        assert _resolve_latest_revision(conn, v2) == v3
        assert _resolve_latest_revision(conn, v3) == v3


def test_recipe_steps_between_materials():
    """'설명' 열 — 자재 사이 공정 안내문(체크리스트식). 등록→상세/TSV→개정 왕복 보존,
    배합 API·기록 상세에 노출. 자재로 오등록되지 않아야 한다. 공식 배합일지 미포함."""
    client = _client()
    headers = _login(client)
    product = f"PSTEP{_uid()}"
    raw = (
        "반제품명\t원료A\t설명\t원료B\t원료C\t설명\t비고\n"
        f"{product}\t60\t개시제 교반 - 300rpm\t30\t10\t반응기 15분 추가 교반\t메모"
    )
    res = client.post("/api/recipes/import", json={"raw_text": raw, "force": True}, headers=headers)
    assert res.status_code == 200, res.text
    rid = res.json()["created_ids"][0]

    # '설명'이 자재로 자동 등록되지 않음
    from src.db import get_connection
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM materials WHERE name='설명'").fetchone() is None

    detail = client.get(f"/api/recipes/{rid}/detail").json()
    assert [it["material_name"] for it in detail["items"]] == ["원료A", "원료B", "원료C"]
    assert detail["steps"] == [
        {"position": 1, "note": "개시제 교반 - 300rpm"},
        {"position": 3, "note": "반응기 15분 추가 교반"},
    ]
    # TSV 왕복: 설명 열이 원위치에 포함
    header = detail["tsv"].split("\n")[0].split("\t")
    assert header == ["반제품명", "원료A", "설명", "원료B", "원료C", "설명", "비고"]

    # 개정(TSV 그대로 재등록) → 설명 보존
    res2 = client.post("/api/recipes/import",
                       json={"raw_text": detail["tsv"], "force": True, "revision_of": rid},
                       headers=headers)
    assert res2.status_code == 200, res2.text
    rid2 = res2.json()["created_ids"][0]
    detail2 = client.get(f"/api/recipes/{rid2}/detail").json()
    assert detail2["steps"] == detail["steps"]

    # 배합 환산 API 에도 steps 노출
    blend = client.get(f"/api/blend/recipes/{rid2}").json()
    assert blend["steps"] == detail["steps"]

    # 기록 저장 → 기록 상세에도 steps (기록 당시 레시피 기준)
    worker = "공정작업" + _uid()[:6]
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    created = client.post("/api/blend/records", json={
        "recipe_id": rid2, "product_name": product, "worker": worker,
        "work_date": "2026-07-08", "total_amount": 100,
        "details": [
            {"material_name": "원료A", "ratio": 60, "theory_amount": 60, "actual_amount": 60},
            {"material_name": "원료B", "ratio": 30, "theory_amount": 30, "actual_amount": 30},
            {"material_name": "원료C", "ratio": 10, "theory_amount": 10, "actual_amount": 10},
        ],
    }, headers=headers)
    assert created.status_code == 200, created.text
    rec = client.get(f"/api/blend/records/{created.json()['id']}").json()
    assert rec["steps"] == detail["steps"]


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


# ---------------- BUG 1: 수정 등록(revision_of) 시 부모 체인 product_code 충돌 회귀 ----------------


def _import_with_code(client, headers, product, a, b, product_code=None, revision_of=None, force=None):
    """product_code/revision_of/force 를 명시적으로 받는 import 헬퍼."""
    body = {"raw_text": f"반제품명\t원료A\t원료B\n{product}\t{a}\t{b}"}
    if product_code is not None:
        body["product_code"] = product_code
    if revision_of is not None:
        body["revision_of"] = revision_of
    if force is not None:
        body["force"] = force
    res = client.post("/api/recipes/import", json=body, headers=headers)
    return res


def test_revision_same_explicit_product_code_as_parent_succeeds():
    """(1) BUG 1: 수정 등록(revision_of) 시 부모와 동일 product_code 명시 → 성공.

    종전: 수정 UI 가 부모 코드를 프리필해 두면, revision 등록이 자기 부모 체인의
    코드와 충돌해 409 로 막혔다(UAPB/B0082 시나리오). 부모 체인 id 를 충돌 조회에서
    제외하도록 고친 뒤 성공해야 한다.
    """
    client = _client()
    headers = _login(client)

    suffix = _uid()
    product = f"REVSCEN{suffix}"
    code = f"BC{suffix[:5]}"  # 영문 2자 접두 + 영숫자

    # 원본을 명시 product_code 로 등록.
    base_res = _import_with_code(client, headers, product, 60, 40, product_code=code)
    assert base_res.status_code == 200, base_res.text
    base_id = base_res.json()["created_ids"][0]

    # 수정 등록 — 부모와 *동일* product_code 를 명시적으로 다시 준다(프리필 흉내).
    rev_res = _import_with_code(
        client, headers, product + "v2", 70, 30,
        product_code=code, revision_of=base_id,
    )
    assert rev_res.status_code == 200, rev_res.text
    rev_id = rev_res.json()["created_ids"][0]

    # 두 버전 모두 같은 코드를 가져야 한다(같은 체인은 코드 공유).
    from src.db import get_connection

    with get_connection() as conn:
        codes = {
            r["id"]: r["product_code"]
            for r in conn.execute(
                "SELECT id, product_code FROM recipes WHERE id IN (?, ?)",
                (base_id, rev_id),
            ).fetchall()
        }
    assert codes[base_id] == code
    assert codes[rev_id] == code


def test_revision_explicit_product_code_held_by_other_chain_still_409():
    """(2) BUG 1 회귀 가드: 다른 체인이 쓰는 코드로 수정 등록 → 여전히 409.

    부모 체인 제외가 '모든 충돌 무시'로 번지지 않았음을 확인 — 제3 체인의 코드는
    여전히 충돌한다.
    """
    client = _client()
    headers = _login(client)

    suffix = _uid()
    code = f"BC{suffix[:5]}"
    # A 체인이 code 를 선점.
    holder = f"OTHERCHAIN{suffix}"
    holder_res = _import_with_code(client, headers, holder, 50, 50, product_code=code)
    assert holder_res.status_code == 200, holder_res.text

    # B 체인 원본 등록(코드 없음) 후, B 의 수정 등록에 A 의 코드를 명시 → 409.
    base2 = _import(client, headers, f"MYCHAIN{suffix}", 60, 40)
    rev_res = _import_with_code(
        client, headers, f"MYCHAIN{suffix}v2", 70, 30,
        product_code=code, revision_of=base2,
    )
    assert rev_res.status_code == 409
    assert holder in rev_res.json()["detail"]
