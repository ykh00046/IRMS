"""item_code_master 임포트 스크립트(tools/import_item_codes.py) 검증.

함수 단위 검증(import_material_master / import_product_master) — subprocess 가 아님.
tmp_path 픽스처로 소형 xlsx 를 openpyxl 로 생성, 실 init_db() 로 스키마를 잡는다.

커버:
  - 원자재: 대분류=원자재 만 들어가고 포장재 행은 skip.
  - 반제품: 제품구분→category_hint 매핑(잉크코드→잉크 등), 소문자 코드 upper 정규화.
  - upsert: 같은 code 재임포트 시 이름 갱신·행 수 불변.
  - 마이그레이션: materials.code UNIQUE(같은 code 두 자재 INSERT 시 IntegrityError),
    NULL 여러 개는 허용(partial unique index).
"""

from __future__ import annotations

import sqlite3
import sys

import openpyxl
import pytest

import src.db.connection as dbconn
from src.db import init_db
from tools.import_item_codes import (
    import_material_master,
    import_product_master,
)


# ---------- 헬퍼: tmp_path 에 실 init_db() 로 스키마(item_code_master 포함) ----------

def _new_conn(tmp_path) -> sqlite3.Connection:
    """tmp_path 산하 Fresh DB 로 init_db() 실행 후 연결 반환.

    get_connection() 은 모듈 전역 DATABASE_PATH/DATA_DIR 를 참조하므로 그것을 tmp_path
    로 치환하면 init_db 가 tmp_path/irms.db 에 스키마를 잡는다(다른 테스트 DB 와 격리).
    """
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "irms.db"
    # 모듈 전역 치환 — get_connection/init_db 가 이 경로를 쓰도록.
    dbconn.DATA_DIR = db_dir
    dbconn.DATABASE_PATH = db_path
    init_db()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 헬퍼: 소형 xlsx 생성 ----------

def _write_material_xlsx(path, rows):
    """code.xlsx 형식: 품목코드/품목명/규격/기준단위/LOT/품목구분/대분류/중분류."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["품목코드", "품목명", "규격", "기준단위", "LOT", "품목구분", "대분류", "중분류"])
    for r in rows:
        ws.append(r)
    wb.save(path)


def _write_product_xlsx(path, rows):
    """code2~4 형식: 품목코드/품명/규격/단위/회계분류/제품구분/..."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["품목코드", "품명", "규격", "단위", "회계분류", "제품구분",
               "상태", "제품명코드", "지정설비", "잉크구분", "유효기간"])
    for r in rows:
        # 6열(제품구분)까진 의미 있음; 나머지는 부족분을 빈 문자열로 채운다.
        full = list(r) + [""] * (11 - len(r))
        ws.append(full[:11])
    wb.save(path)


# ---------- 원자재 임포트 ----------

def test_material_master_only_imports_wonjaejae(tmp_path):
    """배합 원료 계열 prefix(AS/AC/AH/AW)만 적재 — 대분류가 '소모품'이어도 AC/AS 계열은
    실제 배합 자재라 포함(운영 리허설에서 20종 발견). 포장재(CB…)·상품(AA…)은 skip."""
    f = tmp_path / "code.xlsx"
    _write_material_xlsx(f, [
        ("AS0001", "HEMA", "CAS1", "g", "1", "원자재", "원자재", "소프트"),
        ("AC0006", "AIBN", "", "g", "1", "소모품", "소모품", "소프트"),   # 소모품이어도 AC → 포함
        ("CB0001", "BOX", "", "개", "1", "포장재", "포장재", "박스"),      # skip 대상
        ("AA0002", "GOODS", "", "개", "1", "상품", "상품", "상품"),        # skip 대상
    ])
    conn = _new_conn(tmp_path)
    try:
        r = import_material_master(conn, str(f), source="code")
        assert r["imported"] == 2
        assert r["skipped_non_material"] == 2
        rows = conn.execute(
            "SELECT code, name, kind, category_hint FROM item_code_master "
            "WHERE kind='material' ORDER BY code"
        ).fetchall()
        assert [row["code"] for row in rows] == ["AC0006", "AS0001"]
        # category_hint = 대분류/중분류 (원자재 vs 소모품 구분 보존)
        assert rows[0]["category_hint"] == "소모품/소프트"
        assert rows[1]["category_hint"] == "원자재/소프트"
        # 포장재·상품은 마스터에 없어야 한다
        assert conn.execute(
            "SELECT 1 FROM item_code_master WHERE code IN ('CB0001','AA0002')"
        ).fetchone() is None
    finally:
        conn.close()


def test_material_master_skips_empty_code_or_name(tmp_path):
    """빈 이름 행은 skipped_empty, 빈 코드는 prefix 판정 불가 → skipped_non_material."""
    f = tmp_path / "code.xlsx"
    _write_material_xlsx(f, [
        ("AS0001", "HEMA", "", "g", "1", "원자재", "원자재", "소프트"),
        ("", "이름만", "", "g", "1", "원자재", "원자재", "소프트"),  # 빈 코드 → non_material
        ("AS0003", "", "", "g", "1", "원자재", "원자재", "소프트"),  # 빈 이름 → empty
    ])
    conn = _new_conn(tmp_path)
    try:
        r = import_material_master(conn, str(f))
        assert r["imported"] == 1
        assert r["skipped_empty"] == 1
        assert r["skipped_non_material"] == 1
    finally:
        conn.close()


# ---------- 반제품 임포트 ----------

def test_product_master_category_hint_mapping(tmp_path):
    """제품구분(잉크코드/합성코드/약품코드) → IRMS 분류(잉크/합성/약품) 매핑."""
    f = tmp_path / "code2.xlsx"
    _write_product_xlsx(f, [
        ("BC0001", "SOUL", "SOUL", "g", "반제품", "잉크코드"),
        ("B0020", "PB", "PB", "g", "반제품", "합성코드"),
        ("B0001", "38AA", "(38%)", "g", "반제품", "약품코드"),
    ])
    conn = _new_conn(tmp_path)
    try:
        r = import_product_master(conn, str(f), source="code2")
        assert r["imported"] == 3
        rows = {row["code"]: row for row in conn.execute(
            "SELECT code, category_hint FROM item_code_master WHERE kind='product'"
        )}
        assert rows["BC0001"]["category_hint"] == "잉크"
        assert rows["B0020"]["category_hint"] == "합성"
        assert rows["B0001"]["category_hint"] == "약품"
    finally:
        conn.close()


def test_product_master_lowercase_code_upper_normalized(tmp_path):
    """소문자 코드(bc0001) → upper(BC0001) 정규화, 선행/후행 공백 strip."""
    f = tmp_path / "code2.xlsx"
    _write_product_xlsx(f, [
        ("bc0001", "SOUL", "", "g", "반제품", "잉크코드"),
        (" b0020 ", "PB", "", "g", "반제품", "합성코드"),
    ])
    conn = _new_conn(tmp_path)
    try:
        import_product_master(conn, str(f))
        codes = {row["code"] for row in conn.execute("SELECT code FROM item_code_master")}
        assert codes == {"BC0001", "B0020"}
    finally:
        conn.close()


def test_product_master_unknown_gubun_kept_as_is(tmp_path):
    """매핑표에 없는 제품구분 값은 원문 그대로 category_hint."""
    f = tmp_path / "code2.xlsx"
    _write_product_xlsx(f, [
        ("BX0001", "ETC", "", "g", "반제품", "기타코드"),
    ])
    conn = _new_conn(tmp_path)
    try:
        import_product_master(conn, str(f))
        row = conn.execute(
            "SELECT category_hint FROM item_code_master WHERE code='BX0001'"
        ).fetchone()
        assert row["category_hint"] == "기타코드"
    finally:
        conn.close()


# ---------- upsert 멱등성 ----------

def test_material_upsert_updates_name_and_keeps_row_count(tmp_path):
    """같은 code 재임포트 시 이름 갱신, 행수 불변(중복 행 생성 안 함)."""
    f = tmp_path / "code.xlsx"
    _write_material_xlsx(f, [
        ("AS0001", "HEMA(old)", "", "g", "1", "원자재", "원자재", "소프트"),
    ])
    conn = _new_conn(tmp_path)
    try:
        import_material_master(conn, str(f))
        f2 = tmp_path / "code2.xlsx"
        _write_material_xlsx(f2, [
            ("AS0001", "HEMA(new)", "", "g", "1", "원자재", "원자재", "소프트"),
        ])
        import_material_master(conn, str(f2))
        rows = conn.execute(
            "SELECT name FROM item_code_master WHERE code='AS0001'"
        ).fetchall()
        assert len(rows) == 1                  # 행수 불변
        assert rows[0]["name"] == "HEMA(new)"  # 이름 갱신
    finally:
        conn.close()


def test_product_upsert_idempotent(tmp_path):
    """같은 code 재임포트 시 행수 불변, 최신 값 반영."""
    f = tmp_path / "code2.xlsx"
    _write_product_xlsx(f, [
        ("BC0001", "SOUL", "OLD", "g", "반제품", "잉크코드"),
    ])
    conn = _new_conn(tmp_path)
    try:
        import_product_master(conn, str(f))
        f2 = tmp_path / "code3.xlsx"
        _write_product_xlsx(f2, [
            ("BC0001", "SOUL-NEW", "NEW", "g", "반제품", "잉크코드"),
        ])
        import_product_master(conn, str(f2))
        n = conn.execute("SELECT COUNT(*) c FROM item_code_master WHERE code='BC0001'").fetchone()["c"]
        assert n == 1
        row = conn.execute(
            "SELECT name, spec FROM item_code_master WHERE code='BC0001'"
        ).fetchone()
        assert row["name"] == "SOUL-NEW"
        assert row["spec"] == "NEW"
    finally:
        conn.close()


# ---------- 마이그레이션: materials.code UNIQUE ----------

def test_materials_code_unique_on_duplicate(tmp_path):
    """같은 code 두 자재 INSERT → IntegrityError(UNIQUE 인덱스 동작)."""
    conn = _new_conn(tmp_path)
    try:
        conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, code) "
            "VALUES ('A', 'weight', 'g', 'none', 'AS0001')"
        )
        conn.execute(
            "INSERT INTO materials (name, unit_type, unit, color_group, code) "
            "VALUES ('B', 'weight', 'g', 'none', 'AS0001')"
        )
        pytest.fail("같은 code 중복 INSERT 가 IntegrityError 없이 성공하면 안 됨")
    except sqlite3.IntegrityError:
        pass  # 기대
    finally:
        conn.close()


def test_materials_code_allows_multiple_nulls(tmp_path):
    """NULL code 는 여러 개 허용(partial unique index — WHERE code IS NOT NULL)."""
    conn = _new_conn(tmp_path)
    try:
        for n in ("X", "Y", "Z"):
            conn.execute(
                "INSERT INTO materials (name, unit_type, unit, color_group, code) "
                "VALUES (?, 'weight', 'g', 'none', NULL)",
                (n,),
            )
        n = conn.execute("SELECT COUNT(*) c FROM materials WHERE code IS NULL").fetchone()["c"]
        assert n == 3
    finally:
        conn.close()


def test_recipes_product_code_allows_duplicates(tmp_path):
    """반제품 코드는 개정 체인이 공유 → UNIQUE 아님. 같은 code 여러 레시피 허용."""
    conn = _new_conn(tmp_path)
    try:
        for _ in range(3):
            conn.execute(
                "INSERT INTO recipes (product_name, ink_name, status, created_by, created_at, product_code) "
                "VALUES ('PB', 'PB', 'completed', 'tester', '2026-01-01', 'B0020')"
            )
        n = conn.execute("SELECT COUNT(*) c FROM recipes WHERE product_code='B0020'").fetchone()["c"]
        assert n == 3
    finally:
        conn.close()


def test_item_code_master_kind_check_constraint(tmp_path):
    """kind 는 'material' | 'product' 만 — 그 외 CHECK 위반."""
    conn = _new_conn(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO item_code_master (code, name, kind, imported_at) "
                "VALUES ('X', 'X', 'unknown', '2026-01-01')"
            )
    finally:
        conn.close()


# ---------- P6 회귀: --db 비관례 파일명 → 대상에만 스키마/임포트, 관례 DB 무접촉 ----------

def test_import_db_arbitrary_filename_writes_target_not_conventional(tmp_path, monkeypatch):
    """--db 로 비관례 파일명(rehearsal.db)을 주면 임포트가 *그 파일* 에 적용되고
    관례 DB(data/irms.db) 는 건드리지 않는다(P6 회귀).

    과거 버그: init_db() 가 관례 DB 에만 스키마를 잡고 대상은 별도 연결(스키마 누락)을
    열어 — 대상에 item_code_master 가 없거나, 관례 DB 가 오염되었다. _open_target_db 도입으로
    대상 연결에 직접 apply_schema_migrations 가 적용된다.

    대상 DB 는 운영 스냅샷처럼 '기본 테이블은 있되 item_code_master 미적용' 상태를
    모방하기 위해, 관례 DB(init_db 완료)를 복사한 뒤 item_code_master 를 DROP 한다.
    """
    # 관례 DB(data/irms.db) — init_db() 가 스키마(item_code_master 포함, 빈 행)를 잡는다.
    conv_dir = tmp_path / "data"
    conv_dir.mkdir(parents=True, exist_ok=True)
    conv_db = conv_dir / "irms.db"
    dbconn.DATA_DIR = conv_dir
    dbconn.DATABASE_PATH = conv_db
    # main() → _open_target_db 가 os.environ['IRMS_DATA_DIR'] 를 덮어쓸 수 있으므로, monkeypatch
    # 로 원복 보장(안 하면 이후 테스트의 get_connection 이 엉뚱한 경로를 본다).
    monkeypatch.setenv("IRMS_DATA_DIR", str(conv_dir))
    init_db()

    # 임포트 대상 — 비관례 파일명(rehearsal.db). 관례 DB 를 복사해 'pre-P1(마스터 미적용)'
    # 상태로 준비(item_code_master 를 DROP). 베이스 테이블은 그대로(운영 스냅샷 모방).
    target_db = tmp_path / "rehearsal.db"
    import shutil
    # WAL 모드에서는 변경분이 -wal 에 있으므로 체크포인트 후 복사(메인 DB 로 합치기).
    with sqlite3.connect(str(conv_db)) as ck:
        ck.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(conv_db, target_db)
    pre = sqlite3.connect(str(target_db))
    try:
        pre.execute("DROP TABLE IF EXISTS item_code_master")
        pre.commit()
    finally:
        pre.close()

    # 소형 code.xlsx — 원자재 1행(prefix AS).
    xlsx = tmp_path / "code.xlsx"
    _write_material_xlsx(xlsx, [
        ("AS0001", "HEMA", "", "g", "1", "원자재", "원자재", "소프트"),
    ])

    # main() 을 --db <비관례> 로 실행(sys.argv patch).
    import tools.import_item_codes as imp
    monkeypatch.setattr(
        sys, "argv",
        ["import_item_codes.py", "--material", str(xlsx), "--db", str(target_db)],
    )
    rc = imp.main()
    assert rc == 0

    # (1) 대상 DB 에 item_code_master 가 (재)생기고 임포트 행이 들어갔다.
    tconn = sqlite3.connect(str(target_db))
    tconn.row_factory = sqlite3.Row
    try:
        # _open_target_db 가 스키마 마이그레이션을 대상 연결에 적용했는지 확인.
        rows = tconn.execute(
            "SELECT code, name, kind FROM item_code_master ORDER BY code"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["code"] == "AS0001"
        assert rows[0]["name"] == "HEMA"
        assert rows[0]["kind"] == "material"
    finally:
        tconn.close()

    # (2) 관례 DB(data/irms.db) 는 임포트로 오염되지 않았다 — item_code_master 0행 유지.
    cconn = sqlite3.connect(str(conv_db))
    cconn.row_factory = sqlite3.Row
    try:
        n = cconn.execute("SELECT COUNT(*) c FROM item_code_master").fetchone()["c"]
        assert n == 0  # 임포트 행이 관례 DB 로 새지 않음
    finally:
        cconn.close()



def test_import_db_conventional_irmsdb_uses_get_connection(tmp_path, monkeypatch):
    """--db 가 관례 파일명(irms.db) 이면 get_connection() 경로로 임포트(마이그레이션 정상).
    비관례 분기만 바뀐 것이지 관례 경로가 망가지지 않았음을 확인."""
    conv_dir = tmp_path / "data"
    conv_dir.mkdir(parents=True, exist_ok=True)
    conv_db = conv_dir / "irms.db"
    dbconn.DATA_DIR = conv_dir
    dbconn.DATABASE_PATH = conv_db
    # main() → _open_target_db(irms.db 분기) 가 os.environ['IRMS_DATA_DIR'] 를 덮어쓰므로
    # monkeypatch 로 원복 보장(이후 테스트 오염 방지).
    monkeypatch.setenv("IRMS_DATA_DIR", str(conv_dir))
    init_db()

    xlsx = tmp_path / "code.xlsx"
    _write_material_xlsx(xlsx, [
        ("AS0042", "DMA", "", "g", "1", "원자재", "원자재", "소프트"),
    ])

    import tools.import_item_codes as imp
    monkeypatch.setattr(
        sys, "argv",
        ["import_item_codes.py", "--material", str(xlsx), "--db", str(conv_db)],
    )
    assert imp.main() == 0

    cconn = sqlite3.connect(str(conv_db))
    cconn.row_factory = sqlite3.Row
    try:
        row = cconn.execute(
            "SELECT name FROM item_code_master WHERE code='AS0042'"
        ).fetchone()
        assert row is not None and row["name"] == "DMA"
    finally:
        cconn.close()

