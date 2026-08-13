"""tools/audit_item_codes.py 단위 테스트.

각 섹션([A]~[G])이 심어둔 이상 데이터를 검출하는지, 그리고 깨끗한 DB 에서는
모두 '이상 없음'이 나오는지를 검사한다. 임시 DB 와 임시 xlsx 는 pytest 의
tmp_path 픽스처 아래에만 만든다(저장소 루트에 임시 파일을 두지 않는다).

픽스처는 감사 도구가 조회하는 컬럼만 담은 최소 테이블을 직접 CREATE 하고,
[F] 용 xlsx 는 erp_lot_service 파서가 실제로 읽는 열 배치(헤더 14열, 창고/품목코드/
Lot.No/재고)를 따라 openpyxl 로 만든다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from openpyxl import Workbook

from tools import audit_item_codes


# 감사 도구가 읽는 컬럼만 갖춘 최소 스키마. 운영 스키마 전체가 필요 없다.
SCHEMA = """
CREATE TABLE materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    unit_type TEXT,
    unit TEXT,
    color_group TEXT,
    category TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    code TEXT
);
CREATE TABLE item_code_master (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec TEXT,
    unit TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('material', 'product')),
    category_hint TEXT,
    source TEXT,
    imported_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    retired_at TEXT
);
CREATE TABLE recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT,
    product_code TEXT
);
CREATE TABLE blend_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER,
    material_id INTEGER REFERENCES materials(id),
    material_code TEXT,
    material_name TEXT NOT NULL,
    material_lot TEXT,
    actual_amount REAL,
    sequence_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT 'x'
);
"""


def _build_db(path: Path, seeder=None):
    """tmp DB 에 스키마를 잡고 seeder 로 데이터를 채운 뒤 읽기 전용 연결을 돌려준다."""
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    if seeder:
        seeder(conn)
    conn.commit()
    conn.close()
    return audit_item_codes._open_ro(str(path))


def _make_erp_xlsx(path: Path, rows):
    """rows: [(code, lot, stock), ...]. erp_lot_service 파서가 읽는 14열 형식."""
    wb = Workbook()
    ws = wb.active
    ws.append(["창고", "구분", "품목코드", "품목명", "대분류", "중분류", "규격",
               "Lot.No", "기초", "입고", "출고", "재고", "검사대기", "단위"])
    for code, lot, stock in rows:
        # 창고(0) 비어있지 않음, 품목코드(2) 있음, Lot.No(7) 있고 '*' 아님, 재고(11).
        ws.append(["W1", "G1", code, "N" + code, "D", "J", "S",
                   lot, 0, 0, 0, stock, 0, "g"])
    wb.save(str(path))


# ── [A] ─────────────────────────────────────────────────────────────────────

def test_a_active_material_without_code(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M_NULL', NULL, 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M_OK', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M_INACTIVE', NULL, 0)")

    conn = _build_db(tmp_path / "a.db", seed)
    res = audit_item_codes.section_a(conn)
    conn.close()

    assert res["status"] == "issues"
    assert [it["name"] for it in res["items"]] == ["M_NULL"]


# ── [B] ─────────────────────────────────────────────────────────────────────

def test_b_code_not_in_master(tmp_path):
    def seed(c):
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0001', 'A', 'material', 'code', 't')")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M2', 'AS9999', 1)")

    conn = _build_db(tmp_path / "b.db", seed)
    res = audit_item_codes.section_b(conn)
    conn.close()

    assert res["status"] == "issues"
    assert [it["code"] for it in res["items"]] == ["AS9999"]


def test_b_empty_master_skips(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")

    conn = _build_db(tmp_path / "b_empty.db", seed)
    res = audit_item_codes.section_b(conn)
    conn.close()

    assert res["status"] == "skip"
    assert "마스터 미적재" in res["note"]


# ── [C] ─────────────────────────────────────────────────────────────────────

def test_c_code_formatting(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('GOOD', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('WS', '  as0002 ', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('LOWER', 'as0003', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('EMPTY', '', 1)")

    conn = _build_db(tmp_path / "c.db", seed)
    res = audit_item_codes.section_c(conn)
    conn.close()

    assert res["status"] == "issues"
    names = {it["name"] for it in res["items"]}
    assert names == {"WS", "LOWER", "EMPTY"}
    assert "GOOD" not in names


# ── [D] ─────────────────────────────────────────────────────────────────────

def test_b_retired_code_in_use(tmp_path):
    """[B] 폐기(retired) 마스터 코드를 쥔 자재는 별도 목록(retired_in_use)으로 뜬다.

    마스터에 '있다'는 것과 '현행이다'는 다르다 - 임포트 --retire-missing 이후
    폐기 코드를 계속 쥔 자재는 재지정 후보다."""
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M2', 'AS0066', 1)")
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0001', 'A', 'material', 'code', 't')")
        c.execute("INSERT INTO item_code_master"
                  "(code, name, kind, source, imported_at, status, retired_at) "
                  "VALUES ('AS0066', 'PVP', 'material', 'code', 't', "
                  "'retired', '2026-08-13T00:00:00Z')")

    conn = _build_db(tmp_path / "b_r.db", seed)
    res = audit_item_codes.section_b(conn)
    conn.close()

    assert res["items"] == []                      # 마스터에 없는 코드는 없다
    assert [it["code"] for it in res["retired_in_use"]] == ["AS0066"]
    assert res["status"] == "issues"


def test_d_cross_duplicate(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('MatA', 'AS0001', 1)")
        c.execute("INSERT INTO recipes(product_name, product_code) VALUES ('ProdA', 'as0001')")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('MatB', 'AS0099', 1)")
        c.execute("INSERT INTO recipes(product_name, product_code) VALUES ('ProdB', 'PR0001')")

    conn = _build_db(tmp_path / "d.db", seed)
    res = audit_item_codes.section_d(conn)
    conn.close()

    assert res["status"] == "issues"
    assert [it["code_key"] for it in res["items"]] == ["AS0001"]


def test_d_same_name_intermediate_share_is_not_an_issue(tmp_path):
    """이름이 같은 자재-레시피 코드 공유(2단계 중간체)는 문제가 아니라 참고(shares).

    1차 반제품이 2차 배합의 자재로도 등록되는 중간체는 같은 코드를 일부러
    공유한다 - API 교차 중복 차단(_product_code_holder)과 같은 판정 규칙.
    """
    def seed(c):
        # 같은 이름 'PB' - 정상 공유(개정 체인이라 레시피 여러 행).
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('PB', 'B0020', 1)")
        c.execute("INSERT INTO recipes(product_name, product_code) VALUES ('PB', 'B0020')")
        c.execute("INSERT INTO recipes(product_name, product_code) VALUES ('PB', 'B0020')")
        # 다른 이름 - 진짜 충돌.
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('MatX', 'AS0007', 1)")
        c.execute("INSERT INTO recipes(product_name, product_code) VALUES ('ProdY', 'AS0007')")

    conn = _build_db(tmp_path / "d2.db", seed)
    res = audit_item_codes.section_d(conn)
    conn.close()

    assert [it["code_key"] for it in res["items"]] == ["AS0007"]   # 충돌만
    assert [s["code_key"] for s in res["shares"]] == ["B0020"]     # 공유는 참고
    assert res["status"] == "issues"  # 충돌이 있으므로 issues 이지만 B0020 은 아님


# ── [E] ─────────────────────────────────────────────────────────────────────

def test_e_manual_source(tmp_path):
    def seed(c):
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0001', 'A', 'material', 'code', 't')")
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('MN0001', 'M', 'material', 'manual', 't')")

    conn = _build_db(tmp_path / "e.db", seed)
    res = audit_item_codes.section_e(conn)
    conn.close()

    assert res["status"] == "issues"
    assert [it["code"] for it in res["items"]] == ["MN0001"]


# ── [F] ─────────────────────────────────────────────────────────────────────

def test_f_erp_missing_codes(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M2', 'AS0002', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M3', 'AS0003', 1)")

    conn = _build_db(tmp_path / "f.db", seed)
    xlsx = tmp_path / "ERP_2026-08-13.xlsx"
    _make_erp_xlsx(xlsx, [("AS0001", "LOT1", 10), ("AS0002", "LOT2", 20)])

    res = audit_item_codes.section_f(conn, str(xlsx))
    conn.close()

    assert res["status"] == "issues"
    assert [it["code"] for it in res["items"]] == ["AS0003"]
    assert res["file_name"] == "ERP_2026-08-13.xlsx"
    assert res["file_date"] == "2026-08-13"


def test_f_non_raw_codes_excluded(tmp_path):
    """반제품(B 계열)·관리용(BT 등) 코드는 원자재 재고 파일 대상이 아니다.

    ERP 일일 재고 엑셀은 원재료 전용 - 반제품 코드와 ERP 미등록 소모품의 관리용
    코드가 거기 없는 것은 정상이므로 [F] 대조에서 제외하고 개수로만 알린다.
    """
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('원료', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('PB', 'B0020', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('메탄올', 'BT000', 1)")

    conn = _build_db(tmp_path / "f_nr.db", seed)
    xlsx = tmp_path / "ERP_2026-08-13.xlsx"
    _make_erp_xlsx(xlsx, [("AS0001", "LOT1", 10)])

    res = audit_item_codes.section_f(conn, str(xlsx))
    conn.close()

    assert res["status"] == "ok"           # 원자재 AS0001 은 파일에 있음
    assert res["items"] == []              # B0020/BT000 은 오탐으로 뜨지 않는다
    assert res["skipped_non_raw"] == 2


def test_main_auto_picks_latest_erp_file(tmp_path, monkeypatch, capsys):
    """--erp-file 을 생략하면 서버 LOT 검증과 같은 폴더에서 최신 ERP 파일을 자동
    선택한다 - 운영자가 파일명(날짜)을 몰라도 [F] 대조가 돈다."""
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M2', 'AS0002', 1)")

    db = tmp_path / "auto.db"
    _build_db(db, seed).close()
    erp_dir = tmp_path / "erp"
    erp_dir.mkdir()
    _make_erp_xlsx(erp_dir / "ERP_2026-08-12.xlsx", [("AS0001", "L1", 10)])
    _make_erp_xlsx(erp_dir / "ERP_2026-08-13.xlsx", [("AS0001", "L1", 10)])
    monkeypatch.setenv("IRMS_ERP_EXCEL_DIR", str(erp_dir))

    rc = audit_item_codes.main(["--db", str(db), "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)                      # --json 은 JSON 한 덩어리
    sec_f = payload["sections"]["F"]
    assert sec_f["file_name"] == "ERP_2026-08-13.xlsx"   # 최신 파일 자동 선택
    assert [it["code"] for it in sec_f["items"]] == ["AS0002"]


def test_f_no_file_skips(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")

    conn = _build_db(tmp_path / "f_no.db", seed)
    res = audit_item_codes.section_f(conn, None)
    conn.close()

    assert res["status"] == "skip"
    assert "ERP 파일 미지정" in res["note"]


# ── [G] ─────────────────────────────────────────────────────────────────────

def test_g_blend_drift(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0099', 'X', 'material', 'code', 't')")
        # G1: 연결된 자재, 저장코드가 현재 코드와 다름(2행). 단 마스터에는 있어 G2 제외.
        c.execute("INSERT INTO blend_details(material_id, material_code, material_name) "
                  "VALUES (1, 'AS0099', 'M1')")
        c.execute("INSERT INTO blend_details(material_id, material_code, material_name) "
                  "VALUES (1, 'AS0099', 'M1')")
        # 같은 코드 - 드리프트 아님.
        c.execute("INSERT INTO blend_details(material_id, material_code, material_name) "
                  "VALUES (1, 'AS0001', 'M1')")
        # G2: 유령 코드 - 자재에도 마스터에도 없음. material_id NULL(G1 제외).
        c.execute("INSERT INTO blend_details(material_id, material_code, material_name) "
                  "VALUES (NULL, 'GHOST0001', 'Ghost')")

    conn = _build_db(tmp_path / "g.db", seed)
    res = audit_item_codes.section_g(conn)
    conn.close()

    assert res["status"] == "issues"
    g1 = res["g1"]
    assert len(g1) == 1
    assert g1[0]["stored_code"] == "AS0099"
    assert g1[0]["current_code"] == "AS0001"
    assert g1[0]["row_count"] == 2
    g2_codes = [it["code"] for it in res["g2"]]
    assert g2_codes == ["GHOST0001"]


# ── 통합 ──────────────────────────────────────────────────────────────────────

def test_clean_db_all_ok(tmp_path):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M2', 'AS0002', 1)")
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0001', 'A', 'material', 'code', 't')")
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0002', 'B', 'material', 'code', 't')")
        c.execute("INSERT INTO recipes(product_name, product_code) VALUES ('Prod', 'PR0001')")
        c.execute("INSERT INTO blend_details(material_id, material_code, material_name) "
                  "VALUES (1, 'AS0001', 'M1')")

    conn = _build_db(tmp_path / "clean.db", seed)
    xlsx = tmp_path / "ERP_2026-08-13.xlsx"
    _make_erp_xlsx(xlsx, [("AS0001", "LOT1", 10), ("AS0002", "LOT2", 20)])

    res = audit_item_codes.run_audit(conn, str(xlsx))
    conn.close()

    for key in ("A", "B", "C", "D", "E", "F", "G"):
        assert res[key]["status"] == "ok", f"{key}: {res[key]}"


def test_json_output(tmp_path, capsys):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")
        c.execute("INSERT INTO item_code_master(code, name, kind, source, imported_at) "
                  "VALUES ('AS0001', 'A', 'material', 'code', 't')")

    _build_db(tmp_path / "json.db", seed)

    rc = audit_item_codes.main(["--db", str(tmp_path / "json.db"), "--json"])
    out = capsys.readouterr().out

    assert rc == 0
    payload = json.loads(out)
    assert "db" in payload
    assert "sections" in payload
    for key in "ABCDEFG":
        assert key in payload["sections"]


def test_human_output_contains_section_headers(tmp_path, capsys):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")

    _build_db(tmp_path / "human.db", seed)

    rc = audit_item_codes.main(["--db", str(tmp_path / "human.db")])
    out = capsys.readouterr().out

    assert rc == 0
    for key in "ABCDEFG":
        assert f"[{key}]" in out


def test_readonly_does_not_modify_db(tmp_path, capsys):
    def seed(c):
        c.execute("INSERT INTO materials(name, code, is_active) VALUES ('M1', 'AS0001', 1)")

    db = tmp_path / "ro.db"
    _build_db(db, seed)
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    rc = audit_item_codes.main(["--db", str(db), "--json"])
    capsys.readouterr()

    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert rc == 0
    assert before == after, "읽기 전용 도구가 DB 파일을 변경했다"
