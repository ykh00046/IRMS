"""자재 관리 화면(/materials) 이관 + 자재명 수정(과거 기록 이름 전파).

배경: 품목코드는 '레시피 관리'(/management)의 한 탭이었고 자재 LOT 은 별도 메뉴여서
같은 자재를 다루는 화면이 둘로 흩어져 있었다. 레시피 관리는 반제품 도메인이므로
자재 편집이 거기 있을 이유가 없다. /materials 가 둘을 흡수하고 옛 경로는 리다이렉트한다.

자재명 수정(2026-08-13 개편 — 코드 중심·이름 하나 원칙): 종전에는 옛 이름을 동의어로
남겨 읽기 시점에 잇는 방식이었지만, 이름이 계속 여러 개로 불어나는 구조라 폐기했다.
지금은 이름을 바꾸면 과거 배합 기록(blend_details)의 자재명도 함께 새 이름으로 통일한다
— 기록의 정체성(코드·FK·수량)은 그대로고 표기만 바뀐다.

검증:
  1. /materials 가 뜨고 두 탭 마크업이 있다.
  2. /material-lots 는 /materials 로 리다이렉트한다.
  3. 레시피 관리에는 품목코드 탭이 더 이상 없다.
  4. 이름을 바꾸면 과거 기록의 이름이 함께 바뀌고(FK 끊긴 행은 연결까지 복원),
     동의어는 새로 생기지 않으며, 집계는 새 이름 + 같은 코드로 나온다.
  5. 중복 이름·다른 자재의 동의어와 충돌하면 409.
"""

from __future__ import annotations

import importlib
import uuid

import pytest


@pytest.fixture(autouse=True)
def _cleanup_test_master():
    """이 모듈이 남긴 item_code_master 'manual' 행 삭제 — 공유 pytest DB 오염 방지."""
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
    body = {"name": name}
    if code:
        body["code"] = code
    res = client.post("/api/materials", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"]


# ── 1. 화면 ────────────────────────────────────────────────────────────────
def test_materials_page_serves_both_tabs():
    client = _client()
    _login(client)
    res = client.get("/materials")
    assert res.status_code == 200
    html = res.text
    assert 'data-tab="codes"' in html
    assert 'data-tab="lots"' in html
    assert 'id="codes-body"' in html      # 품목코드 표
    assert 'id="mlot-body"' in html       # 자재 LOT 표
    assert "/static/js/materials.js" in html


def test_material_lots_path_redirects_to_materials():
    client = _client()
    res = client.get("/material-lots", follow_redirects=False)
    assert res.status_code in (307, 308), res.text
    assert res.headers["location"] == "/materials"


def test_management_no_longer_has_item_code_tab():
    """레시피 관리에서 품목코드 탭이 사라졌다 — 자재 도메인은 /materials 로 이관."""
    client = _client()
    _login(client)
    html = client.get("/management").text
    assert 'data-tab="codes"' not in html
    assert 'id="codes-body"' not in html
    assert "management/item-codes.js" not in html


def test_materials_page_is_open_to_non_manager_but_hides_code_panel():
    """담당자도 자재 LOT 은 본다. 품목코드 표 마크업은 렌더되지 않는다."""
    client = _client()
    html = client.get("/materials").text
    assert html.count('id="mlot-body"') == 1
    assert 'id="codes-body"' not in html


# ── 2. 자재명 수정 ─────────────────────────────────────────────────────────
def test_rename_propagates_to_past_records_and_keeps_code():
    """이름을 바꾸면 과거 기록의 자재명도 함께 바뀌고, 집계는 새 이름 + 같은 코드.

    FK 가 끊긴 구 이관 기록(material_id NULL)은 이름 전파와 함께 연결도 복원된다.
    동의어는 생기지 않는다 — 코드 중심·이름 하나 원칙(2026-08-13 사용자 결정)."""
    from src.db import get_connection
    from src.services import blend_service

    client = _client()
    headers = _login(client)
    base = _uid()
    code = f"AC{base[:4]}"
    old_name = f"PMA{base}"
    mid = _new_material(client, headers, old_name, code)

    # 옛 이름으로 남은 완료 기록(FK 는 끊긴 상태 — 구 이관 기록에서 흔하다).
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
            "total_amount, status, created_at) "
            "VALUES (?, ?, 'w', '2026-07-20', 100, 'completed', '2026-07-20')",
            (f"LOT{base}", f"제품{base}"),
        )
        rec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO blend_details (blend_record_id, material_id, material_code, "
            "material_name, material_lot, ratio, theory_amount, actual_amount, "
            "sequence_order, created_at) "
            "VALUES (?, NULL, '', ?, 'ML1', 100, 100, 100, 1, '2026-07-20')",
            (rec_id, old_name),
        )
        conn.commit()

    new_name = f"PMA{base}-신규"
    res = client.put(
        f"/api/materials/{mid}/name", json={"name": new_name}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["updated_records"] == 1

    with get_connection() as conn:
        row = conn.execute(
            "SELECT material_name, material_id FROM blend_details "
            "WHERE blend_record_id = ?", (rec_id,)
        ).fetchone()
        # 기록의 이름이 새 이름으로 바뀌고 끊겼던 FK 도 복원됐다.
        assert row["material_name"] == new_name
        assert row["material_id"] == mid
        usage = blend_service.material_usage_periods(
            conn, start_date="2026-07-20", end_date="2026-07-20"
        )
    item = next(i for i in usage["items"] if i["material_name"] == new_name)
    assert item["erp_code"] == code
    # 옛 이름 행은 더 이상 없다 — 이름은 하나.
    assert not [i for i in usage["items"] if i["material_name"] == old_name]

    # 동의어는 생기지 않는다.
    aliases = client.get(f"/api/materials/{mid}/aliases", headers=headers).json()
    assert old_name not in [a["alias_name"] for a in aliases["items"]]


def test_rename_to_own_alias_absorbs_it():
    """자신의 동의어로 개명하면 그 동의어는 정식 이름이 되면서 목록에서 사라진다."""
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"구명{base}", f"AC{base[:4]}")
    alias = f"신명{base}"
    assert client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": alias}, headers=headers
    ).status_code == 200

    res = client.put(
        f"/api/materials/{mid}/name", json={"name": alias}, headers=headers
    )
    assert res.status_code == 200, res.text
    items = client.get(f"/api/materials/{mid}/aliases", headers=headers).json()["items"]
    assert alias not in [a["alias_name"] for a in items]


# ── 2a-2. 기록 표기 흡수(absorb-name) ─────────────────────────────────────
def _insert_legacy_detail(conn, *, name, work_date="2026-07-21"):
    """FK 없이 표기 문자열만 남은 구 이관식 기록 한 행."""
    conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
        "total_amount, status, created_at) "
        "VALUES (?, '제품', 'w', ?, 100, 'completed', ?)",
        (f"L{uuid.uuid4().hex[:8]}", work_date, work_date),
    )
    rec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO blend_details (blend_record_id, material_id, material_code, "
        "material_name, material_lot, ratio, theory_amount, actual_amount, "
        "sequence_order, created_at) "
        "VALUES (?, NULL, '', ?, 'ML1', 100, 100, 100, 1, ?)",
        (rec_id, name, work_date),
    )
    return rec_id


def test_absorb_name_rewrites_records_and_removes_alias():
    """변형 표기 흡수 — 기록의 이름이 정본으로 바뀌고 FK 가 연결되며, 같은 표기의
    옛 동의어는 함께 삭제된다(영구 다리 대신 일회성 통합 — 이름 하나 원칙)."""
    from src.db import get_connection

    client = _client()
    headers = _login(client)
    base = _uid()
    canonical = f"MP{base}"
    variant = f"MEHQ{base}"
    mid = _new_material(client, headers, canonical, f"AC{base[:4]}")
    # 옛 동의어 + 그 표기의 기록 2행.
    assert client.post(
        f"/api/materials/{mid}/aliases", json={"alias_name": variant}, headers=headers
    ).status_code == 200
    with get_connection() as conn:
        r1 = _insert_legacy_detail(conn, name=variant)
        r2 = _insert_legacy_detail(conn, name=variant)
        conn.commit()

    res = client.post(
        f"/api/materials/{mid}/absorb-name", json={"name": variant}, headers=headers
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["absorbed_records"] == 2
    assert payload["alias_removed"] == 1
    assert payload["canonical"] == canonical

    with get_connection() as conn:
        for rid in (r1, r2):
            row = conn.execute(
                "SELECT material_name, material_id FROM blend_details "
                "WHERE blend_record_id = ?", (rid,)
            ).fetchone()
            assert row["material_name"] == canonical
            assert row["material_id"] == mid
    aliases = client.get(f"/api/materials/{mid}/aliases", headers=headers).json()
    assert variant not in [a["alias_name"] for a in aliases["items"]]


def test_absorb_canonical_spelling_relinks_broken_fk():
    """정본 표기 그대로인데 FK 만 끊긴 행도 흡수로 연결된다."""
    from src.db import get_connection

    client = _client()
    headers = _login(client)
    base = _uid()
    canonical = f"PMA{base}"
    mid = _new_material(client, headers, canonical, f"AC{base[:4]}")
    with get_connection() as conn:
        rid = _insert_legacy_detail(conn, name=canonical)
        conn.commit()

    res = client.post(
        f"/api/materials/{mid}/absorb-name", json={"name": canonical}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["absorbed_records"] == 1
    with get_connection() as conn:
        row = conn.execute(
            "SELECT material_id FROM blend_details WHERE blend_record_id = ?", (rid,)
        ).fetchone()
    assert row["material_id"] == mid


def test_absorb_rejects_name_owned_elsewhere():
    """다른 자재의 이름/동의어와 겹치는 표기는 흡수 불가(소유가 갈린다) — 409."""
    client = _client()
    headers = _login(client)
    base = _uid()
    a_id = _new_material(client, headers, f"AAA{base}", f"AC{base[:4]}")
    b_name = f"BBB{base}"
    b_id = _new_material(client, headers, b_name, f"AS{base[:4]}")
    shared = f"별칭{base}"
    assert client.post(
        f"/api/materials/{b_id}/aliases", json={"alias_name": shared}, headers=headers
    ).status_code == 200

    assert client.post(
        f"/api/materials/{a_id}/absorb-name", json={"name": b_name}, headers=headers
    ).status_code == 409
    assert client.post(
        f"/api/materials/{a_id}/absorb-name", json={"name": shared}, headers=headers
    ).status_code == 409


def test_absorb_requires_manager_and_valid_input():
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"자재A{base}", f"AC{base[:4]}")
    assert client.post(
        f"/api/materials/{mid}/absorb-name", json={"name": "  "}, headers=headers
    ).status_code == 400
    anon = _client()
    assert anon.post(
        f"/api/materials/{mid}/absorb-name", json={"name": "X"}
    ).status_code in (401, 403)


# ── 2b. 사용 안 함(비활성) ─────────────────────────────────────────────────
def test_deactivate_hides_from_list_and_reactivate_restores():
    """삭제가 막히는 자재(과거 레시피 참조)의 출구 — 숨기되 데이터는 보존, 되살리기 가능."""
    client = _client()
    headers = _login(client)
    base = _uid()
    name = f"블루안료{base}"
    mid = _new_material(client, headers, name, f"AC{base[:4]}")

    res = client.put(
        f"/api/materials/{mid}/active", json={"is_active": 0}, headers=headers
    )
    assert res.status_code == 200, res.text

    def names(params=None):
        data = client.get("/api/item-codes/materials", params=params or {}, headers=headers).json()
        return [it["name"] for it in data["items"]]

    assert name not in names()                                   # 기본 목록에서 숨김
    assert name in names({"include_inactive": "1"})              # 포함 조회로는 보임

    res = client.put(
        f"/api/materials/{mid}/active", json={"is_active": 1}, headers=headers
    )
    assert res.status_code == 200, res.text
    assert name in names()                                       # 되살리기


def test_set_active_validates_and_requires_manager():
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"자재{base}", f"AC{base[:4]}")
    assert client.put(
        f"/api/materials/{mid}/active", json={"is_active": "yes"}, headers=headers
    ).status_code == 400
    anon = _client()
    assert anon.put(
        f"/api/materials/{mid}/active", json={"is_active": 0}
    ).status_code in (401, 403)


def test_rename_rejects_duplicate_name():
    client = _client()
    headers = _login(client)
    base = _uid()
    a_id = _new_material(client, headers, f"AAA{base}", f"AC{base[:4]}")
    taken = f"BBB{base}"
    _new_material(client, headers, taken, f"AS{base[:4]}")

    res = client.put(
        f"/api/materials/{a_id}/name", json={"name": taken}, headers=headers
    )
    assert res.status_code == 409, res.text


def test_rename_rejects_name_owned_as_another_materials_alias():
    """새 이름이 다른 자재의 동의어면 거부 — 두면 같은 이름이 두 자재를 가리킨다."""
    client = _client()
    headers = _login(client)
    base = _uid()
    a_id = _new_material(client, headers, f"AAA{base}", f"AC{base[:4]}")
    b_id = _new_material(client, headers, f"BBB{base}", f"AS{base[:4]}")
    shared = f"공용{base}"
    assert client.post(
        f"/api/materials/{b_id}/aliases", json={"alias_name": shared}, headers=headers
    ).status_code == 200

    res = client.put(
        f"/api/materials/{a_id}/name", json={"name": shared}, headers=headers
    )
    assert res.status_code == 409, res.text


@pytest.mark.parametrize("bad", ["", "   ", "!!"])
def test_rename_rejects_blank_or_symbol_only(bad):
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")
    res = client.put(f"/api/materials/{mid}/name", json={"name": bad}, headers=headers)
    assert res.status_code == 400, res.text


def test_rename_requires_manager():
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"PMA{base}", f"AC{base[:4]}")

    anon = _client()
    res = anon.put(f"/api/materials/{mid}/name", json={"name": f"X{base}"})
    assert res.status_code in (401, 403), res.text


def test_rename_unknown_material_404():
    client = _client()
    headers = _login(client)
    res = client.put(
        "/api/materials/99999999/name", json={"name": "X"}, headers=headers
    )
    assert res.status_code == 404
