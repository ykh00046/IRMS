"""자재 관리 화면(/materials) 이관 + 자재명 수정(옛 이름 자동 동의어).

배경: 품목코드는 '레시피 관리'(/management)의 한 탭이었고 자재 LOT 은 별도 메뉴여서
같은 자재를 다루는 화면이 둘로 흩어져 있었다. 레시피 관리는 반제품 도메인이므로
자재 편집이 거기 있을 이유가 없다. /materials 가 둘을 흡수하고 옛 경로는 리다이렉트한다.

자재명 수정은 blend_details.material_name 이 기록 시점 문자열로 박제된다는 점 때문에
그냥 두면 위험하다 — 이름을 바꾸는 순간 과거 기록이 품목코드를 잃는다(2026-08-11 에
고친 미매핑 사고와 같은 구조). 그래서 옛 이름을 동의어로 자동 보존한다.

검증:
  1. /materials 가 뜨고 두 탭 마크업이 있다.
  2. /material-lots 는 /materials 로 리다이렉트한다.
  3. 레시피 관리에는 품목코드 탭이 더 이상 없다.
  4. 이름을 바꾸면 옛 이름이 동의어로 남고, 그 이름으로 남은 기록이 계속 코드를 얻는다.
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
def test_rename_keeps_old_name_as_alias_and_preserves_item_code():
    """이름을 바꿔도 옛 이름으로 남은 과거 기록이 같은 품목코드로 계속 집계된다."""
    from src.db import get_connection
    from src.services import blend_service

    client = _client()
    headers = _login(client)
    base = _uid()
    code = f"AC{base[:4]}"
    old_name = f"PMA{base}"
    mid = _new_material(client, headers, old_name, code)

    # 옛 이름으로 남은 완료 기록(FK 는 끊긴 상태 — 현장 기록에서 흔하다).
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
    assert res.json()["alias_kept"] == old_name

    # 옛 이름이 동의어 목록에 있다.
    aliases = client.get(f"/api/materials/{mid}/aliases", headers=headers).json()
    assert old_name in [a["alias_name"] for a in aliases["items"]]

    # 그리고 옛 이름으로 남은 기록이 여전히 품목코드를 얻는다 — 이게 이 기능의 요점.
    with get_connection() as conn:
        usage = blend_service.material_usage_periods(
            conn, start_date="2026-07-20", end_date="2026-07-20"
        )
    item = next(i for i in usage["items"] if i["material_name"] == old_name)
    assert item["erp_code"] == code


def test_rename_can_skip_alias_when_asked():
    """오타 교정처럼 남길 가치가 없으면 keep_alias=false 로 생략한다."""
    client = _client()
    headers = _login(client)
    base = _uid()
    mid = _new_material(client, headers, f"오타{base}", f"AC{base[:4]}")

    res = client.put(
        f"/api/materials/{mid}/name",
        json={"name": f"정상{base}", "keep_alias": False},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["alias_kept"] is None
    assert client.get(f"/api/materials/{mid}/aliases", headers=headers).json()["items"] == []


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
