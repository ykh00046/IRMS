"""바인더 점도 임포트 스크립트 검사.

바인더 종류 정규화(APB(17)→APB17, CSBP→CSPB 오타, 괄호 제거, TEST 제외),
사용한PB 를 material_lot 에 저장(PB 연계 키), 멱등 재임포트를 고정한다.
연계 키 형식이 우리 PB 점도 LOT(8자리)과 그대로 맞물리는 것이 이 기능의 핵심이다.
"""

import importlib

from openpyxl import Workbook


def _make_binder_file(path, pb_prefix="2601"):
    # pb_prefix 로 LOT 을 분기 — conftest 의 DB 는 테스트 간 공유되므로, 각 테스트가
    # 고유한 사용한PB(=lot_no) 를 써야 (product_id, lot_no) UNIQUE 충돌을 피한다.
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("26년도 바인더 점도 기록")
    ws.append(["일자", "바인더", "사용한PB", "점도", "작업자"])
    ws.append(["26년1월5일", "APB", f"{pb_prefix}0501", 409.8, "이시현"])
    ws.append(["26년1월6일", "CSBP", f"{pb_prefix}0601", 78.6, "이재석"])       # 오타 → CSPB
    ws.append(["26년1월6일", "APB(17)", f"{pb_prefix}0602", 384, "이재석"])     # → APB17
    ws.append(["26년1월8일", "APB(TEST)", f"{pb_prefix}0701", 999, "김민준"])   # 제외
    ws.append(["26년1월9일", "APB", f"{pb_prefix}0702", None, "설영훈"])        # 점도 결측 → 제외
    ws.append(["26년1월9일", "APB", "badlot", 400, "설영훈"])                   # PB 형식 이상 → 제외
    wb.save(path)


def test_binder_import_normalizes_and_links(tmp_path, monkeypatch):
    import src.config as cfg

    importlib.reload(cfg)
    from src.db import get_connection
    from src.services import viscosity_service

    xlsx = tmp_path / "바인더.xlsx"
    _make_binder_file(xlsx)

    import scripts.import_binder_viscosity as imp

    stats = imp.import_binder([str(xlsx)])

    # 정규화 결과 — TEST/결측/형식이상 제외, 오타·괄호 병합
    assert stats["inserted"] == 3
    assert stats["products"] == {"APB": 1, "CSPB": 1, "APB17": 1}
    assert stats["skip_binder"] == 1   # APB(TEST)
    assert stats["skip_visc"] == 1     # 점도 결측
    assert stats["skip_pb"] == 1       # badlot

    with get_connection() as conn:
        # CSPB 반제품이 생기고, 사용한PB 가 lot_no·material_lot 양쪽에 들어갔는가
        cspb = viscosity_service.get_product_by_code(conn, "CSPB")
        assert cspb is not None
        row = conn.execute(
            "SELECT lot_no, material_lot, viscosity FROM viscosity_readings WHERE product_id = ?",
            (cspb["id"],),
        ).fetchone()
        assert row["lot_no"] == "26010601"
        assert row["material_lot"] == "26010601"   # PB 연계 키
        assert row["viscosity"] == 78.6
        # APB17 로 정규화됐는가
        assert viscosity_service.get_product_by_code(conn, "APB17") is not None
        assert viscosity_service.get_product_by_code(conn, "CSBP") is None


def test_binder_import_is_idempotent(tmp_path):
    import src.config as cfg

    importlib.reload(cfg)
    xlsx = tmp_path / "바인더.xlsx"
    _make_binder_file(xlsx, pb_prefix="9901")   # 다른 테스트와 LOT 충돌 방지

    import scripts.import_binder_viscosity as imp

    first = imp.import_binder([str(xlsx)])
    second = imp.import_binder([str(xlsx)])
    assert first["inserted"] == 3
    assert second["inserted"] == 0
    assert second["dup"] == 3


def test_binder_normalize_rules():
    import scripts.import_binder_viscosity as imp

    assert imp._normalize_binder("APB(17)") == "APB17"
    assert imp._normalize_binder("APB17") == "APB17"
    assert imp._normalize_binder("CSBP") == "CSPB"
    assert imp._normalize_binder("APB(1)") == "APB"
    assert imp._normalize_binder("CSPB(2)") == "CSPB"
    assert imp._normalize_binder("PM17") == "PM17"
    assert imp._normalize_binder("HSPU") == "HSPU"
    assert imp._normalize_binder("APB(TEST)") is None
    assert imp._normalize_binder("") is None


def test_binder_pb_lot_matches_pb_viscosity_format():
    """연계 키 형식 고정 — 사용한PB 는 우리 PB 점도 LOT(8자리)과 같은 꼴이어야 한다."""
    import scripts.import_binder_viscosity as imp

    assert imp._pb_lot(26010501) == "26010501"   # 정수 셀
    assert imp._pb_lot("26010501") == "26010501"
    assert imp._pb_lot("badlot") is None
    assert imp._pb_lot(None) is None
