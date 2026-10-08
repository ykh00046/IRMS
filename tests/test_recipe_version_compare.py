"""버전 비교 탭 재설계(2026-08-06) — 조회 엔드포인트 라우트 테스트.

by-product current_only=false 가 개정 체인 전체를 반환하고, history/compare 가
전 버전의 자재 행렬(value_weight·change_status)을 내리는지 검증.
신규 서버 라우트 없이 기존 두 엔드포인트로 한 화면이 구성되는지가 핵심이다.
"""

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
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


def _mgr(client):
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    tok = client.cookies.get("csrftoken")
    return {"x-csrftoken": tok} if tok else {}


def _import(client, headers, raw_text, **kw):
    body = {"raw_text": raw_text, "force": True}
    body.update(kw)
    return client.post("/api/recipes/import", json=body, headers=headers)


def test_by_product_current_only_false_returns_all_versions():
    """current_only=false → 개정 체인 전체 버전 반환(현재판만이 아님)."""
    client = _client()
    headers = _mgr(client)
    product = "VCBI" + uuid.uuid4().hex[:6].upper()
    raw = f"반제품명\tA\tB\n{product}\t60\t40"
    v1 = _import(client, headers, raw)
    assert v1.status_code == 200, v1.text
    rid1 = v1.json()["created_ids"][0]
    # 수정 등록(revision_of) — 같은 체인의 새 버전.
    raw2 = f"반제품명\tA\tB\n{product}\t70\t30"
    v2 = _import(client, headers, raw2, revision_of=rid1)
    rid2 = v2.json()["created_ids"][0]

    # current_only=true(기본) → 현재판 1행(rid1 은 대체되어 숨김).
    cur = client.get(f"/api/recipes/by-product?product_name={product}").json()
    assert len(cur["items"]) == 1, "기본 current_only=true 는 현재판 1행"

    # current_only=false → 체인 전체(2행).
    allv = client.get(f"/api/recipes/by-product?product_name={product}&current_only=false").json()
    ids = [it["id"] for it in allv["items"]]
    assert rid1 in ids and rid2 in ids, "current_only=false 는 개정 체인 전체를 반환해야 한다"
    assert len(allv["items"]) >= 2


def test_history_returns_full_chain_with_labels_and_current():
    """/api/recipes/{id}/history → 체인 전체(version_label·is_current·item_count)."""
    client = _client()
    headers = _mgr(client)
    product = "VCHI" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    rid2 = _import(client, headers, f"반제품명\tA\tB\n{product}\t65\t35", revision_of=rid1).json()["created_ids"][0]

    h = client.get(f"/api/recipes/{rid1}/history").json()
    ids = [it["id"] for it in h["items"]]
    assert rid1 in ids and rid2 in ids
    # 현재판 표시(is_current) 가 정확히 하나.
    currents = [it for it in h["items"] if it["is_current"]]
    assert len(currents) == 1
    # version_label 이 각 행에 있다.
    assert all(it.get("version_label") for it in h["items"])


def test_history_compare_returns_material_matrix_with_change_status():
    """/api/recipes/history/compare?ids=... → versions + materials(value_weight·change_status)."""
    client = _client()
    headers = _mgr(client)
    product = "VCCM" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    rid2 = _import(client, headers, f"반제품명\tA\tB\n{product}\t70\t30", revision_of=rid1).json()["created_ids"][0]

    cmp = client.get(f"/api/recipes/history/compare?ids={rid1},{rid2}").json()
    assert len(cmp["versions"]) == 2
    # 자재 A·B 가 행렬에 있다.
    names = {m["material_name"] for m in cmp["materials"]}
    assert "A" in names and "B" in names
    # 각 자재의 values 에 value_weight 가 있다(비율 계산용).
    mat_a = next(m for m in cmp["materials"] if m["material_name"] == "A")
    weights = [v["value_weight"] for v in mat_a["values"] if v["value_weight"] is not None]
    assert 60.0 in weights or 70.0 in weights, "value_weight 가 행렬에 있어야 비율 계산이 가능하다"
    # change_status 가 same/modified/partial 중 하나.
    assert all(m["change_status"] in ("same", "modified", "partial") for m in cmp["materials"])


def test_history_compare_requires_same_chain():
    """서로 다른 체인의 id 를 섞으면 400(DIFFERENT_CHAINS)."""
    client = _client()
    headers = _mgr(client)
    p1 = "VCD1" + uuid.uuid4().hex[:6].upper()
    p2 = "VCD2" + uuid.uuid4().hex[:6].upper()
    r1 = _import(client, headers, f"반제품명\tA\n{p1}\t100").json()["created_ids"][0]
    r2 = _import(client, headers, f"반제품명\tA\n{p2}\t100").json()["created_ids"][0]
    res = client.get(f"/api/recipes/history/compare?ids={r1},{r2}")
    assert res.status_code == 400


def test_history_compare_keeps_repeated_material_rows():
    """같은 자재가 한 판에 여러 행이면 행마다 따로 내린다(material_id 로 덮어쓰지 않는다).

    2026-10-08 운영 신고: 투입 단계가 다른 PMA 4행이 버전 비교에서 1행·한 배합량만 보였다.
    """
    client = _client()
    headers = _mgr(client)
    product = "VCDU" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\tA\n{product}\t60\t30\t10").json()["created_ids"][0]
    rid2 = _import(
        client, headers, f"반제품명\tA\tB\tA\n{product}\t50\t30\t20", revision_of=rid1
    ).json()["created_ids"][0]

    cmp = client.get(f"/api/recipes/history/compare?ids={rid1},{rid2}").json()
    rows_a = [m for m in cmp["materials"] if m["material_name"] == "A"]
    assert len(rows_a) == 2, "A 가 2행이면 비교표에도 2행"
    assert [m["occurrence"] for m in rows_a] == [1, 2]
    assert all(m["occurrence_count"] == 2 for m in rows_a)
    assert len({m["row_key"] for m in cmp["materials"]}) == 3
    first = {v["version_id"]: v["value_weight"] for v in rows_a[0]["values"]}
    second = {v["version_id"]: v["value_weight"] for v in rows_a[1]["values"]}
    assert (first[rid1], first[rid2]) == (60.0, 50.0)
    assert (second[rid1], second[rid2]) == (10.0, 20.0)
    assert all(m["change_status"] == "modified" for m in rows_a)
    # B 는 한 행뿐이라 번호 표기 대상이 아니다.
    row_b = next(m for m in cmp["materials"] if m["material_name"] == "B")
    assert row_b["occurrence_count"] == 1 and row_b["change_status"] == "same"


def test_history_items_carry_linked_record_count():
    """history 각 판에 linked_record_count(연결 배합 기록 수)가 있다. 새 판은 0."""
    client = _client()
    headers = _mgr(client)
    product = "VCLR" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    _import(client, headers, f"반제품명\tA\tB\n{product}\t70\t30", revision_of=rid1)

    h = client.get(f"/api/recipes/{rid1}/history").json()
    assert len(h["items"]) == 2
    assert all(it["linked_record_count"] == 0 for it in h["items"])


def test_revert_registers_old_content_as_new_current_version():
    """되돌리기: 옛 판(v1)의 tsv 를 현재판(v2)의 개정으로 등록하면 v3 이 현재판이고 내용은 v1."""
    client = _client()
    headers = _mgr(client)
    product = "VCRV" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    rid2 = _import(
        client, headers, f"반제품명\tA\tB\n{product}\t70\t30", revision_of=rid1
    ).json()["created_ids"][0]

    tsv = client.get(f"/api/recipes/{rid1}/detail").json()["tsv"]
    res = _import(client, headers, tsv, revision_of=rid2, created_by="레시피 관리")
    assert res.status_code == 200, res.text
    rid3 = res.json()["created_ids"][0]

    h = client.get(f"/api/recipes/{rid1}/history").json()
    assert [it["id"] for it in h["items"]][-1] == rid3
    v3 = next(it for it in h["items"] if it["id"] == rid3)
    assert v3["is_current"] and v3["version_label"] == "v3"
    assert sum(1 for it in h["items"] if it["is_current"]) == 1

    def weights(rid):
        items = client.get(f"/api/recipes/{rid}/detail").json()["items"]
        return sorted((it["material_name"], float(it["value"])) for it in items)

    assert weights(rid3) == weights(rid1) == [("A", 60.0), ("B", 40.0)]


def test_delete_old_version_leaves_single_current():
    """삭제: v1→v2(v2 현재)에서 v1 을 지우면 이력은 현재판 1개만 남는다."""
    client = _client()
    headers = _mgr(client)
    product = "VCDL" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    rid2 = _import(
        client, headers, f"반제품명\tA\tB\n{product}\t70\t30", revision_of=rid1
    ).json()["created_ids"][0]

    res = client.delete(f"/api/recipes/{rid1}?delete_blend_records=false", headers=headers)
    assert res.status_code == 200, res.text

    h = client.get(f"/api/recipes/{rid2}/history").json()
    assert len(h["items"]) == 1
    assert h["items"][0]["id"] == rid2 and h["items"][0]["is_current"]


def test_version_name_set_clear_and_exposed():
    """PUT version-name: 지정하면 history·detail 에 실리고, 빈 값은 해제(NULL), 41자는 400."""
    client = _client()
    headers = _mgr(client)
    product = "VCVN" + uuid.uuid4().hex[:6].upper()
    rid1 = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    rid2 = _import(
        client, headers, f"반제품명\tA\tB\n{product}\t70\t30", revision_of=rid1
    ).json()["created_ids"][0]

    res = client.put(
        f"/api/recipes/{rid1}/version-name", json={"version_name": "  저점도용  "}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "recipe_id": rid1, "version_name": "저점도용"}

    h = client.get(f"/api/recipes/{rid2}/history").json()
    names = {it["id"]: it["version_name"] for it in h["items"]}
    assert names == {rid1: "저점도용", rid2: None}
    assert client.get(f"/api/recipes/{rid1}/detail").json()["version_name"] == "저점도용"
    cmp = client.get(f"/api/recipes/history/compare?ids={rid1},{rid2}").json()
    assert {v["id"]: v["version_name"] for v in cmp["versions"]} == {rid1: "저점도용", rid2: None}

    res = client.put(f"/api/recipes/{rid1}/version-name", json={"version_name": "   "}, headers=headers)
    assert res.status_code == 200 and res.json()["version_name"] is None
    assert client.get(f"/api/recipes/{rid1}/detail").json()["version_name"] is None

    res = client.put(f"/api/recipes/{rid1}/version-name", json={"version_name": "가" * 41}, headers=headers)
    assert res.status_code == 400
    assert res.json()["detail"] == "버전 이름은 40자 이내입니다."

    res = client.put("/api/recipes/99999999/version-name", json={"version_name": "x"}, headers=headers)
    assert res.status_code == 404


def test_history_compare_accepts_single_version():
    """한 판뿐인 체인도 비교 API가 행렬을 내린다(정리로 한 판만 남은 뒤 버전 비교가 비던 회귀)."""
    client = _client()
    headers = _mgr(client)
    product = "VCS1" + uuid.uuid4().hex[:6].upper()
    rid = _import(client, headers, f"반제품명\tA\tB\n{product}\t60\t40").json()["created_ids"][0]
    res = client.get(f"/api/recipes/history/compare?ids={rid}")
    assert res.status_code == 200, res.text
    cmp = res.json()
    assert [v["id"] for v in cmp["versions"]] == [rid]
    assert {m["material_name"] for m in cmp["materials"]} == {"A", "B"}
