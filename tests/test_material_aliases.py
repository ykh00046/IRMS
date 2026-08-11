"""자재 동의어(별칭) 관리 API — 등록·해제·충돌 가드 (item_code_routes A6).

배경: 품목코드는 자재 1행이 배타 소유한다. 그래서 같은 원재료가 배합 기록에 다른
이름으로 남으면(예: PMA 를 'Propylene glycol monomethyl etheracetate' 로 기록) 그 행은
품목코드 없이 자재 사용량 API 로 나가고, 상위 재고 대시보드가 조용히 버린다.
동의어로 그 이름을 자재에 이어 해결한다 — 코드를 옮기지도, 자재를 합치지도 않는다.

검증:
  1. 등록 → 목록에 뜨고, 자재 목록의 alias_count 가 오른다.
  2. 등록한 동의어로 남은 배합 기록이 그 자재의 품목코드로 해석된다(끝단 효과).
  3. 다른 자재의 '이름'과 겹치는 동의어는 409 — 그 이름은 이미 그 자재로 해석되므로
     가로채면 실적이 엉뚱한 코드로 간다.
  4. 다른 자재의 동의어와 겹쳐도 409, 같은 자재에 중복 등록해도 409.
  5. 자재명과 같은 이름은 400(무의미).
  6. 해제 후 다시 미매핑으로 돌아간다.
  7. 담당자(비책임자)는 등록할 수 없다.

test_two_stage_item_code.py 의 client/management-login 헬퍼 패턴을 따른다.
"""

from __future__ import annotations

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    """이 모듈이 남긴 item_code_master 'manual' 행 삭제 — 공유 pytest DB 오염 방지.

    test_two_stage_item_code.py 와 동일 패턴. POST /materials 가 _ensure_master_entry 로
    만드는 source='manual' 행이 남으면 다른 파일의 임포트 판정이 바뀐다.
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


def _new_material(client, headers, name, code=None):
    """자재 하나 등록하고 id 반환. code 는 형식(영문 1~2자 + 영숫자 2~8자) 준수 필요."""
    body = {"name": name}
    if code:
        body["code"] = code
    res = client.post("/api/materials", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"]


# ── 1. 등록 → 목록·개수 반영 ────────────────────────────────────────────────
def test_alias_add_shows_in_list_and_bumps_count():
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")

    long_name = f"Propylene glycol monomethyl etheracetate {base}"
    res = client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": long_name}, headers=headers
    )
    assert res.status_code == 200, res.text

    listed = client.get(f"/api/materials/{mid}/aliases", headers=headers).json()
    assert [a["alias_name"] for a in listed["items"]] == [long_name]

    mats = client.get("/api/item-codes/materials", headers=headers).json()["items"]
    row = next(m for m in mats if m["id"] == mid)
    assert row["alias_count"] == 1


# ── 2. 끝단 효과 — 동의어로 남은 기록이 그 자재의 품목코드로 해석된다 ───────
def test_alias_makes_recorded_name_resolve_to_item_code():
    """동의어 등록 전에는 코드 없이 나가던 자재명이, 등록 후 자재의 코드로 집계된다."""
    from src.db import get_connection
    from src.services import blend_service

    client = _client()
    headers = _login(client)
    base = _uid()
    code = f"AC{base[:4]}"
    mid = _new_material(client, headers, f"PMA{base}", code)
    recorded_name = f"Propylene glycol monomethyl etheracetate {base}"

    # material_id 를 비운 채(=현장 기록에서 FK 가 끊긴 상태) 이름만 남은 완료 기록 1건.
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
            "total_amount, status, created_at) "
            "VALUES (?, ?, 'w', '2026-07-15', 100, 'completed', '2026-07-15')",
            (f"LOT{base}", f"제품{base}"),
        )
        rec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO blend_details (blend_record_id, material_id, material_code, "
            "material_name, material_lot, ratio, theory_amount, actual_amount, "
            "sequence_order, created_at) "
            "VALUES (?, NULL, '', ?, 'ML1', 100, 100, 100, 1, '2026-07-15')",
            (rec_id, recorded_name),
        )
        conn.commit()

    def erp_code_of():
        with get_connection() as conn:
            res = blend_service.material_usage_periods(
                conn, start_date="2026-07-15", end_date="2026-07-15"
            )
        item = next(i for i in res["items"] if i["material_name"] == recorded_name)
        return item["erp_code"], res

    before, res_before = erp_code_of()
    assert before == "", "동의어 등록 전에는 코드가 붙지 않아야 한다"
    # 표면화 — 코드 없는 행이 조용히 사라지지 않고 응답에 드러난다.
    assert res_before["unmapped_count"] >= 1
    assert recorded_name in [
        m["material_name"] for m in res_before["unmapped_materials"]
    ]

    res = client.post(
        f"/api/materials/{mid}/aliases",
        json={"alias_name": recorded_name},
        headers=headers,
    )
    assert res.status_code == 200, res.text

    after, res_after = erp_code_of()
    assert after == code, "동의어 등록 후에는 자재의 품목코드로 해석돼야 한다"
    assert recorded_name not in [
        m["material_name"] for m in res_after["unmapped_materials"]
    ]


# ── 3. 다른 자재의 '이름'과 겹치면 409 ──────────────────────────────────────
def test_alias_conflicting_with_another_material_name_is_rejected():
    """이미 다른 자재의 이름인 문자열은 동의어로 쓸 수 없다(실적이 엉뚱한 코드로 감)."""
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")
    other_name = f"NVP{base}"
    _new_material(client, headers, other_name, f"AS{base[:4]}")

    res = client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": other_name}, headers=headers
    )
    assert res.status_code == 409, res.text
    assert other_name in res.json()["detail"]

    # 대소문자·공백만 다른 변형도 같은 정규화 키라 막혀야 한다.
    res2 = client.post(
        f"/api/materials/{mid}/aliases",
        json={"alias_name": f" {other_name.lower()} "},
        headers=headers,
    )
    assert res2.status_code == 409, res2.text


# ── 4. 동의어 중복 ─────────────────────────────────────────────────────────
def test_alias_duplicates_are_rejected():
    client = _client()
    headers = _login(client)
    base = _uid()
    a_id = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")
    b_id = _new_material(client, headers, f"NVP{base}", f"AS{base[:4]}")
    alias = f"동의어{base}"

    assert client.post(
        f"/api/materials/{a_id}/aliases", json={"alias_name": alias}, headers=headers
    ).status_code == 200

    # 같은 자재에 또 → 409
    again = client.post(
        f"/api/materials/{a_id}/aliases", json={"alias_name": alias}, headers=headers
    )
    assert again.status_code == 409
    assert "이미 등록된 동의어" in again.json()["detail"]

    # 다른 자재에 같은 이름 → 409(소유 자재를 지목)
    other = client.post(
        f"/api/materials/{b_id}/aliases", json={"alias_name": alias}, headers=headers
    )
    assert other.status_code == 409
    assert f"PMA{base}" in other.json()["detail"]


# ── 5. 자재명과 같은 이름은 400 ────────────────────────────────────────────
def test_alias_same_as_own_name_is_rejected():
    client = _client()
    headers = _login(client)
    base = _uid()
    name = f"PMA{base}"
    mid = _new_material(client, headers, name, f"AC{base[:4]}")

    res = client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": name}, headers=headers
    )
    assert res.status_code == 400
    assert "자재명과 같은" in res.json()["detail"]


# ── 5b. 빈 값·기호만 → 400 ─────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["", "   ", "---", "!!"])
def test_alias_blank_or_symbol_only_is_rejected(bad):
    """기호만 남는 입력은 normalize_token 이 빈 문자열이라 어떤 기록과도 매칭될 수 없다."""
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")

    res = client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": bad}, headers=headers
    )
    assert res.status_code == 400, res.text


# ── 6. 해제 ────────────────────────────────────────────────────────────────
def test_alias_delete_removes_mapping():
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")
    alias = f"동의어{base}"

    add = client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": alias}, headers=headers
    )
    alias_id = add.json()["id"]

    res = client.delete(f"/api/materials/{mid}/aliases/{alias_id}", headers=headers)
    assert res.status_code == 200, res.text
    assert client.get(f"/api/materials/{mid}/aliases", headers=headers).json()["items"] == []

    # 없는 동의어 해제 → 404
    assert client.delete(
        f"/api/materials/{mid}/aliases/{alias_id}", headers=headers
    ).status_code == 404


def test_alias_delete_scoped_to_owning_material():
    """다른 자재의 동의어를 id 만으로 지울 수 없다(material_id 를 조건에 함께 둔다)."""
    client = _client()
    headers = _login(client)
    base = _uid()
    a_id = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")
    b_id = _new_material(client, headers, f"NVP{base}", f"AS{base[:4]}")
    add = client.post(
        f"/api/materials/{a_id}/aliases",
        json={"alias_name": f"동의어{base}"},
        headers=headers,
    )
    alias_id = add.json()["id"]

    assert client.delete(
        f"/api/materials/{b_id}/aliases/{alias_id}", headers=headers
    ).status_code == 404
    # 원래 자재에는 그대로 남아 있어야 한다.
    assert len(client.get(f"/api/materials/{a_id}/aliases", headers=headers).json()["items"]) == 1


# ── 7. 권한 — 로그인 없이는 등록 불가 ──────────────────────────────────────
def test_alias_requires_manager():
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")

    anon = _client()
    res = anon.post(f"/api/materials/{mid}/aliases", json={"alias_name": f"X{base}"})
    assert res.status_code in (401, 403), res.text


# ── 8. 없는 자재 → 404 ─────────────────────────────────────────────────────
def test_alias_unknown_material_404():
    client = _client()
    headers = _login(client)
    assert client.get("/api/materials/99999999/aliases", headers=headers).status_code == 404
    assert client.post(
        "/api/materials/99999999/aliases", json={"alias_name": "X"}, headers=headers
    ).status_code == 404
