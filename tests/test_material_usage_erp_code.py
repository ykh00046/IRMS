"""erp_code 해석 3단 경로 확장 검증 (blend_service).

배경: _resolve_erp_code 가 자재명 문자열로만 materials.code 를 찾던 구멍.
blend_details.material_id FK 와 material_aliases 동의어 경로를 추가해,
"Propylene glycol monomethyl etheracetate"(=PMA) 같은 동의어가 코드 없이
나가는 일을 막는다.

검증 케이스:
  (a) blend_details.material_id 만 채워지고 material_name 은 마스터에 없는 이름인 행
      → fk_code(1순위) 로 materials.code 가 나와야 한다.
  (b) material_id 는 NULL 이고 material_name 이 마스터 자재명과 일치하는 행
      → materials.code(2순위) 로 해석돼야 한다.
  (c) material_id 는 NULL 이고 material_name 이 material_aliases.alias_name 과만 일치하는 행
      → alias_code_map(3순위) 으로 materials.code 가 나와야 한다.
  (d) 같은 자재명이 material_id 유무로 두 행 나뉘어도 집계가 한 행으로 합쳐지는지
      (GROUP BY 에 fk_code/m.code 가 들어가면 쪼개지는 회귀 방지).

스키마는 test_blend_material_code._make_db 를 재사용(materials.code·material_aliases
·blend_details.material_id 포함). 기존 tests/ 의 인메모리 SQLite 픽스처 관행을 따른다.
"""

from __future__ import annotations

import sqlite3

from src.services import blend_service as bs

# P4 스키마(materials.code·material_aliases·blend_details.material_id 포함) 재사용.
from tests.test_blend_material_code import _make_db


def _insert_blend_record(conn: sqlite3.Connection, *, product: str, work_date: str) -> int:
    """완료 배합 기록 한 건을 넣고 id 반환. 자재 없는 껍데기 — 상세는 호출부가 채운다."""
    conn.execute(
        "INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
        "total_amount, status, created_at) "
        "VALUES (?, ?, 'w', ?, 100, 'completed', ?)",
        (f"{product}{work_date.replace('-', '')}01", product, work_date, work_date),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_detail(
    conn: sqlite3.Connection,
    *,
    blend_record_id: int,
    material_name: str,
    material_id: int | None,
    material_code: str = "",
    actual_amount: float = 100.0,
    sequence_order: int = 1,
) -> None:
    """blend_details 한 행 삽입. material_id NULL 여부로 3단 경로를 시나리오별로 만든다."""
    conn.execute(
        "INSERT INTO blend_details (blend_record_id, material_id, material_code, "
        "material_name, material_lot, ratio, theory_amount, actual_amount, "
        "sequence_order, created_at) "
        "VALUES (?, ?, ?, ?, ?, 100, ?, ?, ?, '2026-07-01')",
        (
            blend_record_id, material_id, material_code, material_name, f"L-{material_name}",
            actual_amount, actual_amount, sequence_order,
        ),
    )


# ── (a) material_id 만 있고 material_name 은 마스터에 없는 이름 ──────────────
def test_erp_code_prefers_fk_code_when_material_id_set():
    """material_id 가 살아 있으면 그 자재의 materials.code 가 1순위로 나온다.

    material_name 이 마스터에 없는 이름(오타·별칭)이라도 FK 가 직결하면 가장 먼저 믿는다.
    """
    conn = _make_db()
    # 마스터 자재 'PMA'(code PM0001) — 이 이름은 기록에 쓰이지 않는다.
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('PMA', 'weight', 'g', '용제', 'PM0001')"
    )
    pma_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 기록의 material_name 은 마스터명('PMA')이 아닌 긴 화학명. material_id 만 직결.
    brid = _insert_blend_record(conn, product="P제품", work_date="2026-07-01")
    _insert_detail(
        conn, blend_record_id=brid,
        material_name="Propylene glycol monomethyl etheracetate",
        material_id=pma_id, material_code="",
    )

    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert len(res["items"]) == 1
    assert res["items"][0]["erp_code"] == "PM0001"  # fk_code 1순위


# ── (b) material_id NULL, material_name == 마스터 자재명 ──────────────────────
def test_erp_code_falls_back_to_material_name_when_no_fk():
    """material_id 가 없어도 material_name 이 마스터명과 일치하면 materials.code(2순위)."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('HEMA (Lotte)', 'weight', 'g', '단량체', 'HM0055')"
    )

    brid = _insert_blend_record(conn, product="H제품", work_date="2026-07-02")
    _insert_detail(
        conn, blend_record_id=brid,
        material_name="HEMA (Lotte)", material_id=None, material_code="",
    )

    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert len(res["items"]) == 1
    assert res["items"][0]["erp_code"] == "HM0055"  # materials.code 2순위


# ── (c) material_id NULL, material_name == alias_name ─────────────────────────
def test_erp_code_resolves_via_alias_when_name_not_in_master():
    """material_name 이 material_aliases.alias_name 과만 일치 → alias_code_map(3순위).

    마스터명('PMA')은 기록에 없고, 긴 화학명이 별칭으로만 등록된 자재의 코드가 잡혀야 한다.
    """
    conn = _make_db()
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('PMA', 'weight', 'g', '용제', 'PM0001')"
    )
    pma_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) "
        "VALUES (?, 'Propylene glycol monomethyl etheracetate')",
        (pma_id,),
    )

    brid = _insert_blend_record(conn, product="A제품", work_date="2026-07-03")
    _insert_detail(
        conn, blend_record_id=brid,
        material_name="Propylene glycol monomethyl etheracetate",
        material_id=None, material_code="",
    )

    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert len(res["items"]) == 1
    assert res["items"][0]["erp_code"] == "PM0001"  # alias_code_map 3순위


# ── (d) 같은 자재명이 material_id 유무로 두 행 → 집계 한 행 합치기 ───────────
def test_same_material_name_aggregates_across_fk_presence():
    """같은 material_name 이 material_id 있는 행·없는 행으로 나뉘어도 한 행으로 합쳐진다.

    GROUP BY 에 fk_code/m.code 가 들어가면 material_id 유무로 쪼개지는 회귀를 잡는다.
    한쪽은 FK 로 PM0001, 다른쪽은 FK 없지만 마스터명('PMA')으로 PM0001 — 어느 쪽이든
    같은 코드로 귀결하고 양쪽 실적이 한 줄에 합쳐진다.
    """
    conn = _make_db()
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('PMA', 'weight', 'g', '용제', 'PM0001')"
    )
    pma_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # 행1: material_id 직결(FK 경로)
    brid1 = _insert_blend_record(conn, product="P1", work_date="2026-07-01")
    _insert_detail(
        conn, blend_record_id=brid1, material_name="PMA",
        material_id=pma_id, material_code="", actual_amount=100.0, sequence_order=1,
    )
    # 행2: 같은 material_name 이지만 material_id NULL(이름 경로)
    brid2 = _insert_blend_record(conn, product="P2", work_date="2026-07-01")
    _insert_detail(
        conn, blend_record_id=brid2, material_name="PMA",
        material_id=None, material_code="", actual_amount=50.0, sequence_order=1,
    )

    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    # 한 행으로 합쳐야 한다 — GROUP BY 가 material_id/fk_code 로 쪼개지지 않았다는 증거.
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["material_name"] == "PMA"
    assert item["erp_code"] == "PM0001"
    assert item["total_actual"] == 150.0  # 100 + 50
    assert item["batch_count"] == 2       # 서로 다른 배합 기록 2건


# ── material_usage_details 도 동일 3단 경로를 타는지 ─────────────────────────
def test_material_usage_details_uses_fk_and_alias_paths():
    """행 단위 상세(material_usage_details) 도 fk_code·alias_code_map 경로를 쓴다.

    batch_details.material_id → materials.code 맵(id→code) 으로 fk_code 를 뽑아 넘기는지,
    그리고 alias_code_map 도 함께 넘기는지 확인한다. 한 기록에 (a)+(c) 두 시나리오를 섞는다.
    """
    conn = _make_db()
    conn.execute(
        "INSERT INTO materials (name, unit_type, unit, category, code) "
        "VALUES ('PMA', 'weight', 'g', '용제', 'PM0001')"
    )
    pma_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) "
        "VALUES (?, 'Propylene glycol monomethyl etheracetate')",
        (pma_id,),
    )

    brid = _insert_blend_record(conn, product="P제품", work_date="2026-07-01")
    # 행1: material_id 직결(FK 경로) — 마스터에 없는 이름이어도 FK 로 코드.
    _insert_detail(
        conn, blend_record_id=brid, material_name="오타이름",
        material_id=pma_id, material_code="", actual_amount=70.0, sequence_order=1,
    )
    # 행2: material_id NULL, material_name == alias_name(별칭 경로).
    _insert_detail(
        conn, blend_record_id=brid, material_name="Propylene glycol monomethyl etheracetate",
        material_id=None, material_code="", actual_amount=30.0, sequence_order=2,
    )

    res = bs.material_usage_details(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert res["total"] == 2
    by_name = {i["material_name"]: i for i in res["items"]}
    assert by_name["오타이름"]["erp_code"] == "PM0001"              # fk_code 경로
    assert by_name["Propylene glycol monomethyl etheracetate"]["erp_code"] == "PM0001"  # alias 경로


# ── 외부 연동 계약: erp_code 는 코드 형태일 때만 나간다 ──────────────────────
def test_erp_code_blank_when_resolution_yields_non_code():
    """폴백 끝단이 코드 아닌 문자열을 내면 erp_code 는 빈 값 + unmapped 표면화.

    'RM 형태 자재명' 폴백(6순위)은 공백 섞인 자재명("RM 용제 A")도 그대로 반환했다.
    코드 아닌 값이 erp_code 로 나가면 상위 재고 대시보드가 조용히 오매칭하고,
    빈 값과 달리 unmapped_* 에도 안 잡힌다 — 폴백(4-8순위)에만 적용되는 형태
    가드가 빈 값으로 돌려 정비 신호(unmapped)로 흐르게 한다. materials.code
    직결(1-3순위)은 운영자가 부여한 정본이라 형태와 무관하게 그대로 나간다."""
    conn = _make_db()
    brid = _insert_blend_record(conn, product="R제품", work_date="2026-07-01")
    _insert_detail(
        conn, blend_record_id=brid, material_name="RM 용제 A",
        material_id=None, material_code="",
    )

    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert len(res["items"]) == 1
    assert res["items"][0]["erp_code"] == ""          # 코드 형태 아님 → 빈 값
    assert res["unmapped_count"] == 1                  # unmapped 로 표면화


def test_erp_code_rm_shaped_name_still_passes():
    """공백 없는 RM 계열 값("RM123")은 레거시 코드 형태로 보고 그대로 내보낸다."""
    conn = _make_db()
    brid = _insert_blend_record(conn, product="R제품", work_date="2026-07-01")
    _insert_detail(
        conn, blend_record_id=brid, material_name="RM123",
        material_id=None, material_code="",
    )

    res = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert res["items"][0]["erp_code"] == "RM123"
    assert res["unmapped_count"] == 0


def test_material_usage_responses_carry_resolved_at():
    """집계·상세 응답 모두 erp_code 해석 기준 시각(resolved_at)을 싣는다.

    코드 지정·동의어 등록이 과거 기록에 소급 적용되므로(의도된 설계) 같은 기간을
    다른 날 조회하면 값이 달라질 수 있다 — 소비자가 기준 시각을 알아야 한다."""
    conn = _make_db()
    brid = _insert_blend_record(conn, product="P제품", work_date="2026-07-01")
    _insert_detail(
        conn, blend_record_id=brid, material_name="PMA",
        material_id=None, material_code="",
    )

    periods = bs.material_usage_periods(conn, start_date="2026-07-01", end_date="2026-07-31")
    details = bs.material_usage_details(conn, start_date="2026-07-01", end_date="2026-07-31")
    assert periods["resolved_at"]
    assert details["resolved_at"]


# ── _alias_code_map 단위 — OperationalError 빈 dict 폴백 ─────────────────────
def test_alias_code_map_returns_empty_when_table_missing():
    """material_aliases 테이블이 없는 구버전/테스트 DB 는 빈 dict 폴백(방어 패턴)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # materials.code 컬럼만 있는 materials, material_aliases 는 아예 없음.
    conn.execute("CREATE TABLE materials (id INTEGER PRIMARY KEY, name TEXT, code TEXT)")
    assert bs._alias_code_map(conn) == {}
