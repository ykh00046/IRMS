"""자재 LOT 이력(lot_history_service) — 레시피 가족·자재 축의 LOT 교체 시점 판정.

판정 규칙(서비스 모듈 docstring)을 하나씩 못 박는다. 규칙이 흔들리면 화면이 "교체"를
없는 데서 만들어 내거나(빈 LOT·취소·기간 첫 기록) 있는 걸 놓친다(개정으로 가족 끊김).
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid

from src.services import lot_history_service as lh


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            category TEXT,
            revision_of INTEGER
        );
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL, recipe_id INTEGER, product_name TEXT NOT NULL,
            worker TEXT NOT NULL, work_date TEXT NOT NULL,
            total_amount REAL NOT NULL DEFAULT 100,
            status TEXT NOT NULL DEFAULT 'completed'
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL,
            material_code TEXT, material_name TEXT NOT NULL, material_lot TEXT,
            sequence_order INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def _recipe(conn, name, *, status="completed", revision_of=None) -> int:
    cur = conn.execute(
        "INSERT INTO recipes (product_name, status, revision_of) VALUES (?, ?, ?)",
        (name, status, revision_of),
    )
    return int(cur.lastrowid)


def _record(conn, *, date, product, recipe_id=None, lot=None, status="completed",
            details=(), worker="작업자") -> int:
    lot = lot or f"{product}-{date.replace('-', '')}-{uuid.uuid4().hex[:4]}"
    cur = conn.execute(
        "INSERT INTO blend_records (product_lot, recipe_id, product_name, worker, work_date, status)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lot, recipe_id, product, worker, date, status),
    )
    rid = int(cur.lastrowid)
    for i, (name, mlot, *rest) in enumerate(details):
        code = rest[0] if rest else None
        conn.execute(
            "INSERT INTO blend_details (blend_record_id, material_code, material_name, material_lot,"
            " sequence_order) VALUES (?, ?, ?, ?, ?)",
            (rid, code, name, mlot, i),
        )
    return rid


def _row(data, material):
    return next(r for r in data["rows"] if r["material_name"] == material)


def test_change_detected_between_consecutive_batches():
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("PB", "RM-01"), ("MP", "A1")])
    _record(conn, date="2026-06-10", product="APB", recipe_id=r, details=[("PB", "RM-01"), ("MP", "A1")])
    _record(conn, date="2026-07-01", product="APB", recipe_id=r, details=[("PB", "RM-02"), ("MP", "A1")])

    data = lh.lot_history(conn, family=f"r:{r}")
    assert data["record_count"] == 3
    assert data["change_count"] == 1
    c = data["changes"][0]
    assert (c["work_date"], c["material_name"], c["prev_lot"], c["new_lot"]) == (
        "2026-07-01", "PB", "RM-01", "RM-02")
    pb = _row(data, "PB")
    assert [s["lot"] for s in pb["segments"]] == ["RM-01", "RM-02"]
    assert pb["segments"][0]["record_count"] == 2
    assert pb["segments"][0]["last_date"] == "2026-06-10"
    assert _row(data, "MP")["change_count"] == 0
    # 교체가 많은 자재가 위로.
    assert data["rows"][0]["material_name"] == "PB"


def test_blank_lot_is_a_segment_but_not_a_change():
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("PB", "RM-01")])
    _record(conn, date="2026-06-05", product="APB", recipe_id=r, details=[("PB", "")])
    _record(conn, date="2026-06-09", product="APB", recipe_id=r, details=[("PB", None)])
    _record(conn, date="2026-06-12", product="APB", recipe_id=r, details=[("PB", "RM-01")])
    _record(conn, date="2026-06-20", product="APB", recipe_id=r, details=[("PB", "RM-02")])

    data = lh.lot_history(conn, family=f"r:{r}")
    segs = _row(data, "PB")["segments"]
    assert [s["lot"] for s in segs] == ["RM-01", None, "RM-01", "RM-02"]
    assert segs[1]["record_count"] == 2
    # 빈 칸을 사이에 둔 RM-01→RM-01 은 교체가 아니고, RM-01→RM-02 만 한 번.
    assert [(c["prev_lot"], c["new_lot"]) for c in data["changes"]] == [("RM-01", "RM-02")]


def test_lot_compare_ignores_case_and_whitespace_but_keeps_reverts():
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("PB", "rm-01")])
    _record(conn, date="2026-06-02", product="APB", recipe_id=r, details=[("PB", " RM-01 ")])
    _record(conn, date="2026-06-03", product="APB", recipe_id=r, details=[("PB", "RM-02")])
    _record(conn, date="2026-06-04", product="APB", recipe_id=r, details=[("PB", "RM-01")])

    data = lh.lot_history(conn, family=f"r:{r}")
    assert [s["lot"] for s in _row(data, "PB")["segments"]] == ["rm-01", "RM-02", "RM-01"]
    # A→B→A 는 두 번의 교체로 남긴다(혼용 시기 파악용).
    assert data["change_count"] == 2


def test_canceled_records_are_ignored():
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("PB", "RM-01")])
    _record(conn, date="2026-06-02", product="APB", recipe_id=r, status="canceled",
            details=[("PB", "RM-99")])
    _record(conn, date="2026-06-03", product="APB", recipe_id=r, details=[("PB", "RM-01")])

    data = lh.lot_history(conn, family=f"r:{r}")
    assert data["change_count"] == 0
    assert data["record_count"] == 2


def test_revision_chain_is_one_family_and_label_is_active_tip():
    """NPR → NPR-S2 → NPR-S 처럼 이름이 다른 개정 체인도 한 가족. 이름은 현재 활성본."""
    conn = _make_db()
    root = _recipe(conn, "NPR")
    mid = _recipe(conn, "NPR-S2", revision_of=root)
    tip = _recipe(conn, "NPR-S", revision_of=mid)
    _record(conn, date="2026-05-01", product="NPR", recipe_id=root, details=[("PB", "RM-01")])
    _record(conn, date="2026-06-01", product="NPR-S2", recipe_id=mid, details=[("PB", "RM-01")])
    _record(conn, date="2026-07-01", product="NPR-S", recipe_id=tip, details=[("PB", "RM-02")])

    fams = lh.list_families(conn)["items"]
    assert len(fams) == 1
    assert fams[0]["key"] == f"r:{root}"
    assert fams[0]["label"] == "NPR-S"
    assert fams[0]["record_count"] == 3
    assert [m["name"] for m in fams[0]["materials"]] == ["PB"]

    data = lh.lot_history(conn, family=f"r:{root}")
    assert data["family"]["label"] == "NPR-S"
    assert data["family"]["recipe_ids"] == [root, mid, tip]
    # 개정을 건너 하나의 연속 이력: 2026-07-01 에 RM-01→RM-02 한 번.
    assert [(c["work_date"], c["prev_lot"], c["new_lot"]) for c in data["changes"]] == [
        ("2026-07-01", "RM-01", "RM-02")]


def test_unlinked_records_join_family_by_product_name_or_stand_alone():
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-05-01", product="APB", recipe_id=None, details=[("PB", "RM-00")])   # 구 이관
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("PB", "RM-01")])
    _record(conn, date="2026-06-02", product="LEGACY", recipe_id=None, details=[("PB", "RM-01")])

    fams = {f["key"]: f for f in lh.list_families(conn)["items"]}
    assert set(fams) == {f"r:{r}", "p:LEGACY"}
    assert fams[f"r:{r}"]["record_count"] == 2

    data = lh.lot_history(conn, family=f"r:{r}")
    assert [(c["prev_lot"], c["new_lot"]) for c in data["changes"]] == [("RM-00", "RM-01")]

    legacy = lh.lot_history(conn, family="p:LEGACY")
    assert legacy["family"]["label"] == "LEGACY"
    assert legacy["record_count"] == 1


def test_material_only_query_crosses_recipes_and_matches_by_code_or_name():
    conn = _make_db()
    a = _recipe(conn, "APB")
    b = _recipe(conn, "CSPB")
    _record(conn, date="2026-06-01", product="APB", recipe_id=a, details=[("PB", "RM-01", "PB-CODE")])
    _record(conn, date="2026-06-05", product="CSPB", recipe_id=b, details=[("PB", "RM-01", "PB-CODE")])
    _record(conn, date="2026-06-10", product="APB", recipe_id=a, details=[("PB", "RM-02", "PB-CODE")])
    _record(conn, date="2026-06-20", product="CSPB", recipe_id=b, details=[("PB", "RM-02", "PB-CODE")])
    _record(conn, date="2026-06-21", product="CSPB", recipe_id=b, details=[("MP", "A1")])

    by_code = lh.lot_history(conn, material="c:PB-CODE")
    assert {r["family_label"] for r in by_code["rows"]} == {"APB", "CSPB"}
    assert [(c["work_date"], c["family_label"]) for c in by_code["changes"]] == [
        ("2026-06-20", "CSPB"), ("2026-06-10", "APB")]
    # 레시피 전체 조회는 레시피 이름순.
    assert [r["family_label"] for r in by_code["rows"]] == ["APB", "CSPB"]

    by_name = lh.lot_history(conn, material="pb")
    assert by_name["change_count"] == 2
    assert lh.lot_history(conn, material="MP")["change_count"] == 0


def test_material_key_prefers_code_and_keeps_latest_name():
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("MEHQ", "L1", "MP-CODE")])
    _record(conn, date="2026-06-02", product="APB", recipe_id=r, details=[("MP", "L2", "MP-CODE")])

    data = lh.lot_history(conn, family=f"r:{r}")
    assert len(data["rows"]) == 1
    assert data["rows"][0]["material_name"] == "MP"
    assert data["rows"][0]["material_code"] == "MP-CODE"
    assert data["change_count"] == 1


def test_date_range_filters_after_computing_full_history():
    """기간 첫 기록은 교체가 아니다 — 가족 전체를 세운 뒤 기간으로 자른다."""
    conn = _make_db()
    r = _recipe(conn, "APB")
    _record(conn, date="2026-05-01", product="APB", recipe_id=r, details=[("PB", "RM-01")])
    _record(conn, date="2026-06-01", product="APB", recipe_id=r, details=[("PB", "RM-02")])
    _record(conn, date="2026-07-01", product="APB", recipe_id=r, details=[("PB", "RM-02")])
    _record(conn, date="2026-08-01", product="APB", recipe_id=r, details=[("PB", "RM-03")])

    data = lh.lot_history(conn, family=f"r:{r}", start_date="2026-06-15", end_date="2026-07-31")
    assert data["change_count"] == 0
    assert data["record_count"] == 1
    segs = _row(data, "PB")["segments"]
    # 기간에 걸친 구간만 남고, 그 구간의 실제 시작·끝은 자르지 않는다.
    assert [(s["lot"], s["first_date"], s["last_date"]) for s in segs] == [
        ("RM-02", "2026-06-01", "2026-07-01")]

    later = lh.lot_history(conn, family=f"r:{r}", start_date="2026-08-01")
    assert [(c["prev_lot"], c["new_lot"]) for c in later["changes"]] == [("RM-02", "RM-03")]


def test_requires_family_or_material():
    conn = _make_db()
    try:
        lh.lot_history(conn)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("family/material 둘 다 비면 ValueError 여야 한다")


def test_unknown_family_returns_empty_not_error():
    conn = _make_db()
    data = lh.lot_history(conn, family="r:999999")
    assert data["rows"] == [] and data["changes"] == []
    assert data["family"]["label"] == "r:999999"


# ── 라우트 ──────────────────────────────────────────────────────────────────

def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_routes_families_history_export_and_400():
    client = _client()
    res = client.get("/api/blend/lot-history/families")
    assert res.status_code == 200
    assert "items" in res.json() and "materials" in res.json()

    res = client.get("/api/blend/lot-history")
    assert res.status_code == 400

    res = client.get("/api/blend/lot-history", params={"material": "없는자재" + uuid.uuid4().hex[:6]})
    assert res.status_code == 200
    assert res.json()["rows"] == []

    res = client.get("/api/blend/lot-history/export", params={"material": "PB"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    page = client.get("/lot-history")
    assert page.status_code == 200
    assert "LOT 이력" in page.text and "lh-timeline" in page.text
