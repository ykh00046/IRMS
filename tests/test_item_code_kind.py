"""품목코드 계열(code_kind) 판정 + ERP LOT 검사 대상 스킵 검증.

배경: ERP 일일 재고 엑셀은 원재료(AS/AC/AH/AW) 전용이다. 반제품(B 계열)·ERP 미등록
소모품의 관리용 코드(BT000 등)는 LOT 검사 대상이 아니므로 check_lot 가 파일을 열기
전에 valid=True(reason='not_erp_managed') 로 스킵한다 — "ERP에 없는 품목코드" 경고
소음을 끊는다. /materials 품목코드 탭은 각 자재의 code_kind(raw/product/managed/None)
를 보여줘 운영자가 원자재/반제품/관리용을 오해하지 않게 한다.

커버:
  1) check_lot not_erp_managed 스킵 — BT000/B0020(반제품)/소문자 bt000 는 스킵,
     원자재 코드(AS/AC)는 기존대로 검사 대상.
  2) GET /item-codes/materials code_kind — raw/product/managed/None 판정(대소문자 무시).
     단일 SELECT 로 마스터를 한 번 읽는 경로(item_code_routes._code_kind) 검증.
"""

import importlib
import uuid

import pytest
from openpyxl import Workbook


# ── check_lot: ERP 파일 격리 픽스처 (test_material_lots 패턴) ─────────────────


HEADERS_ROW = (
    "창고", "구분", "품목코드", "품목명", "대분류", "중분류", "규격",
    "Lot.No", "기초", "입고", "출고", "재고", "검사대기", "단위",
)


def _write_erp_file(path, rows):
    """rows(각 행은 14열 튜플/리스트) 로 ERP 파일을 만든다. 헤더 자동 추가."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet"
    ws.append(list(HEADERS_ROW))
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    wb.close()


@pytest.fixture
def isolated_erp_dir(tmp_path, monkeypatch):
    """빈 임시 디렉터리를 IRMS_ERP_EXCEL_DIR 로 지정 + 캐시 초기화.

    not_erp_managed 스킵은 파일 로드 전에 일어나므로 파일 무관이지만, 원자재 코드가
    '여전히 검사 대상'임을 확인하려면 결정론적인 파일 상태가 필요하다.
    """
    monkeypatch.setenv("IRMS_ERP_EXCEL_DIR", str(tmp_path))
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    return tmp_path


def test_check_lot_non_raw_code_skipped(isolated_erp_dir):
    """관리용 코드(BT000)·반제품 코드(B0020)는 LOT 검사 대상이 아니라 스킵된다.

    valid=True 이므로 배합 화면은 경고를 띄우지 않는다. 파일을 아예 열지 않는다
    (file_name=None, file_ok=True).
    """
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    with get_connection() as conn:
        bt = svc.check_lot(conn, "BT000", "ANYLOT")
        b = svc.check_lot(conn, "B0020", "ANYLOT")
    assert bt["reason"] == "not_erp_managed"
    assert bt["valid"] is True
    assert bt["source"] is None
    assert bt["file_name"] is None
    assert bt["file_ok"] is True
    assert b["reason"] == "not_erp_managed"
    assert b["valid"] is True


def test_check_lot_non_raw_code_case_insensitive(isolated_erp_dir):
    """소문자 코드도 strip().upper() 기준으로 스킵된다."""
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    with get_connection() as conn:
        result = svc.check_lot(conn, "bt000", "ANYLOT")
    assert result["reason"] == "not_erp_managed"
    assert result["valid"] is True


def test_check_lot_raw_code_still_checked(isolated_erp_dir):
    """원자재 계열(AS/AC/AH/AW) 코드는 기존대로 LOT 검사 대상 — 스킵되지 않는다.

    파일에 AC0001/LOT-A(재고 100) 가 있으면 ok(valid, source=erp). 같은 호출이
    not_erp_managed 였다면 source 는 None 이고 file_name 도 None 이다.
    """
    _write_erp_file(
        isolated_erp_dir / "ERP_2026-01-02.xlsx",
        [
            ("원료창고", "원자재", "AC0001", "자재A", "원자재", "그룹", "규격", "LOT-A",
             0, 0, 0, 100, 0, "g"),
            ("원료창고", "원자재", "AS0052", "자재S", "원자재", "그룹", "규격", "LOT-S",
             0, 0, 0, 50, 0, "g"),
        ],
    )
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        ok = svc.check_lot(conn, "AC0001", "LOT-A")
        miss = svc.check_lot(conn, "AS0052", "NOPE")  # 코드는 있으나 LOT 없음
    assert ok["reason"] == "ok"
    assert ok["source"] == "erp"
    assert ok["file_name"] == "ERP_2026-01-02.xlsx"
    # 원자재 코드는 검사 흐름으로 들어가 not_erp_managed 가 아니다.
    assert miss["reason"] == "lot_not_in_file"
    assert miss["reason"] != "not_erp_managed"


def test_check_lot_empty_code_not_skipped(isolated_erp_dir):
    """빈 코드는 not_erp_managed 조건(코드가 비어있지 않고)에 해당하지 않아 스킵되지 않는다.

    빈 코드는 정상 흐름으로 넘어가 파일 없음(fail-open) 처리된다.
    """
    from src.db import get_connection
    import src.services.erp_lot_service as svc

    svc.reset_cache()
    with get_connection() as conn:
        result = svc.check_lot(conn, "", "LOT-A")
    assert result["reason"] != "not_erp_managed"


# ── code_kind: 라우터(GET /item-codes/materials) 판정 ────────────────────────
# test_item_code_admin 의 _client/_login/_seed_material 패턴을 따른다.


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


def _uid():
    return uuid.uuid4().hex[:8].upper()


def _seed_material(conn, name, code=None):
    cur = conn.execute(
        "INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code) "
        "VALUES (?, 'weight', 'g', 'none', NULL, 1, ?)",
        (name, code),
    )
    conn.commit()
    return cur.lastrowid


def _seed_master(conn, code, name, kind):
    """item_code_master 행 삽입(source='test_item_code_kind' 로 표시해 종료 후 정리)."""
    conn.execute(
        "INSERT INTO item_code_master (code, name, kind, category_hint, source, imported_at) "
        "VALUES (?, ?, ?, NULL, 'test_item_code_kind', '2026-08-13T00:00:00Z')",
        (code, name, kind),
    )
    conn.commit()


def _cleanup(conn, material_ids):
    conn.execute(
        "DELETE FROM item_code_master WHERE source = 'test_item_code_kind'"
    )
    for mid in material_ids:
        conn.execute("DELETE FROM materials WHERE id = ?", (mid,))
    conn.commit()


def test_code_kind_raw_product_managed_none():
    """code_kind 판정 — raw/product/managed/None 네 경우.

      - 원자재 계열(AS/AC/AH/AW) → 'raw'
      - 마스터에 kind='product' 로 있는 코드 → 'product'
      - 그 외(관리용 BT 등) → 'managed'
      - 코드 없음 → None
    """
    client = _client()
    headers = _login(client)
    uid = _uid()

    raw_code = f"AS{uid}"          # 원자재
    prod_code = f"B{uid}0020"      # 반제품(마스터 product 행 있음)
    managed_code = f"BT{uid}000"   # 관리용(마스터 product 행 없음)

    from src.db import get_connection

    with get_connection() as conn:
        raw_id = _seed_material(conn, f"원자재자재{uid}", code=raw_code)
        prod_id = _seed_material(conn, f"반제품자재{uid}", code=prod_code)
        managed_id = _seed_material(conn, f"관리용자재{uid}", code=managed_code)
        none_id = _seed_material(conn, f"코드없는자재{uid}", code=None)
        _seed_master(conn, prod_code, f"반제품명{uid}", "product")
        ids = [raw_id, prod_id, managed_id, none_id]

    try:
        res = client.get(
            "/api/item-codes/materials", params={"q": uid}, headers=headers
        )
        assert res.status_code == 200, res.text
        by_id = {it["id"]: it for it in res.json()["items"]}
        assert by_id[raw_id]["code_kind"] == "raw"
        assert by_id[prod_id]["code_kind"] == "product"
        assert by_id[managed_id]["code_kind"] == "managed"
        assert by_id[none_id]["code_kind"] is None
    finally:
        with get_connection() as conn:
            _cleanup(conn, ids)


def test_code_kind_case_insensitive():
    """마스터의 product 코드를 대소문자 무시로 비교 — 소문자 마스터 코드도 product 로 잡는다."""
    client = _client()
    headers = _login(client)
    uid = _uid()

    # 마스터에는 소문자로, 자재 코드는 대문자로 저장된 과거 데이터 상황.
    prod_code_lower = f"bc{uid}001"
    prod_code_upper = prod_code_lower.upper()

    from src.db import get_connection

    with get_connection() as conn:
        prod_id = _seed_material(conn, f"대소문자반제품{uid}", code=prod_code_upper)
        _seed_master(conn, prod_code_lower, f"반제품명{uid}", "product")
        ids = [prod_id]

    try:
        res = client.get(
            "/api/item-codes/materials", params={"q": uid}, headers=headers
        )
        assert res.status_code == 200, res.text
        by_id = {it["id"]: it for it in res.json()["items"]}
        assert by_id[prod_id]["code_kind"] == "product"
    finally:
        with get_connection() as conn:
            _cleanup(conn, ids)


def test_code_kind_lowercase_raw_code_still_raw():
    """원자재 코드가 소문자로 저장돼도 prefix 판정(upper 기준)으로 raw."""
    client = _client()
    headers = _login(client)
    uid = _uid()

    from src.db import get_connection

    with get_connection() as conn:
        raw_id = _seed_material(conn, f"소문자원자재{uid}", code=f"ac{uid}001")
        ids = [raw_id]

    try:
        res = client.get(
            "/api/item-codes/materials", params={"q": uid}, headers=headers
        )
        assert res.status_code == 200, res.text
        by_id = {it["id"]: it for it in res.json()["items"]}
        assert by_id[raw_id]["code_kind"] == "raw"
    finally:
        with get_connection() as conn:
            _cleanup(conn, ids)
