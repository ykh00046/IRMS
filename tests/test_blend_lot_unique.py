"""product_lot 채번 경쟁 + UNIQUE 봉인 (감사 F-1) 회귀 테스트."""

from __future__ import annotations

import sqlite3
import threading
import time

from src.db.migrations import dedup_product_lots
from src.services import blend_service as bs


_SCHEMA = """
CREATE TABLE blend_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_lot TEXT NOT NULL, recipe_id INTEGER, product_name TEXT NOT NULL,
    ink_name TEXT, position TEXT, worker TEXT NOT NULL, work_date TEXT NOT NULL,
    work_time TEXT, total_amount REAL NOT NULL, scale TEXT,
    status TEXT NOT NULL DEFAULT 'completed', note TEXT, reactor INTEGER,
    manual_entry INTEGER NOT NULL DEFAULT 0,
    is_bulk_regenerated INTEGER NOT NULL DEFAULT 0,
    worker_sign TEXT, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE blend_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER NOT NULL, material_id INTEGER,
    material_code TEXT, material_name TEXT NOT NULL, material_lot TEXT,
    ratio REAL, theory_amount REAL, actual_amount REAL,
    sequence_order INTEGER NOT NULL DEFAULT 0,
    manual_entry INTEGER NOT NULL DEFAULT 0,
    carried_over INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE viscosity_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, lot_no TEXT,
    viscosity REAL, measured_date TEXT, created_at TEXT, blend_record_id INTEGER
);
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
    actor_user_id INTEGER, actor_username TEXT, actor_display_name TEXT,
    actor_access_level TEXT, target_type TEXT, target_id TEXT, target_label TEXT,
    details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_blend_records_lot_unique ON blend_records(product_lot);
"""


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _file_db(path) -> sqlite3.Connection:
    conn = _connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    return conn


def _create(conn, *, product="잉크A", date="2026-07-12"):
    return bs.create_blend_record(
        conn, recipe_id=None, product_name=product, ink_name=None, position=None,
        worker="QA", work_date=date, work_time=None, total_amount=100.0, scale=None,
        note=None, details=[{"material_name": "A", "ratio": 100,
                             "theory_amount": 100, "actual_amount": 100}],
        created_by="QA", created_at="2026-07-12T00:00:00Z",
    )


def test_concurrent_create_yields_distinct_lots(tmp_path):
    """[red 재현] 수정 전: 두 스레드가 같은 max_seq 를 읽어 동일 LOT 2행이 저장됐다.
    수정 후: BEGIN IMMEDIATE 직렬화로 항상 서로 다른 순번이 발번된다."""
    db = tmp_path / "race.db"
    _file_db(db).close()

    orig = bs.generate_product_lot

    def slow_generate(connection, product_name, work_date):
        lot = orig(connection, product_name, work_date)
        time.sleep(0.2)  # 채번→INSERT 사이 경쟁 창을 벌린다
        return lot

    bs.generate_product_lot, results, errors = slow_generate, [], []

    def worker():
        conn = _connect(db)
        try:
            rid = _create(conn)
            conn.commit()
            results.append(rid)
        except Exception as exc:  # noqa: BLE001 — 수정 전 거동 관찰용
            errors.append(exc)
        finally:
            conn.close()

    try:
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        bs.generate_product_lot = orig

    assert not errors, errors
    check = _connect(db)
    lots = [r["product_lot"] for r in check.execute(
        "SELECT product_lot FROM blend_records ORDER BY id")]
    check.close()
    assert len(lots) == 2 and len(set(lots)) == 2, lots  # 수정 전: 동일 LOT 2개 → red


def test_create_retries_on_unique_violation(tmp_path, monkeypatch):
    """UNIQUE 위반 시 재채번 재시도로 저장이 성공한다."""
    db = tmp_path / "retry.db"
    conn = _file_db(db)
    first = _create(conn)          # 잉크A26071201
    conn.commit()
    taken = conn.execute("SELECT product_lot FROM blend_records WHERE id=?", (first,)).fetchone()[0]

    orig, calls = bs.generate_product_lot, {"n": 0}

    def dup_first(connection, product_name, work_date):
        calls["n"] += 1
        return taken if calls["n"] == 1 else orig(connection, product_name, work_date)

    monkeypatch.setattr(bs, "generate_product_lot", dup_first)
    _create(conn)
    conn.commit()
    lots = {r["product_lot"] for r in conn.execute("SELECT product_lot FROM blend_records")}
    conn.close()
    assert calls["n"] >= 2 and len(lots) == 2


def test_dedup_product_lots_renumbers_and_relinks(tmp_path):
    """마이그레이션 보조: 중복 그룹의 최소 id 보존, 나머지 재채번 + 점도 lot_no 동기 + 감사 기록."""
    conn = _file_db(tmp_path / "dedup.db")
    conn.execute("DROP INDEX idx_blend_records_lot_unique")  # 중복이 있던 '수정 전' DB 재연
    ins = ("INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
           "total_amount, created_at) VALUES (?, ?, 'QA', ?, 100, 't')")
    conn.execute(ins, ("잉크A26071201", "잉크A", "2026-07-12"))                 # id=1 — 보존
    conn.execute(ins, ("잉크A26071201", "잉크A", "2026-07-12"))                 # id=2 — 재채번 대상
    conn.execute(ins, ("잉크A26071202", "잉크A", "2026-07-12"))                 # 02는 이미 점유
    conn.execute(ins, ("LEGACY-라벨", "잉크B", "2026-07-12"))                    # id=4
    conn.execute(ins, ("LEGACY-라벨", "잉크B", "2026-07-12"))                    # id=5 — 비정규 재채번
    conn.execute("INSERT INTO viscosity_readings (product_id, lot_no, viscosity, created_at, "
                 "blend_record_id) VALUES (1, '잉크A26071201', 50, 't', 2)")     # id=2 에 물린 점도
    conn.execute("INSERT INTO viscosity_readings (product_id, lot_no, viscosity, created_at, "
                 "blend_record_id) VALUES (1, '잉크A26071201', 51, 't', NULL)")  # 미연계 — 보존
    changes = dedup_product_lots(conn)

    rows = {int(r["id"]): r["product_lot"] for r in conn.execute(
        "SELECT id, product_lot FROM blend_records")}
    assert rows[1] == "잉크A26071201"            # 최소 id 보존
    assert rows[2] == "잉크A26071203"            # 02 점유 → 다음 빈 순번 03
    assert rows[5] == "LEGACY-라벨-2"            # 비정규 → 접미
    assert {c["id"] for c in changes} == {2, 5}
    # 물린 점도만 lot_no 동기, 미연계 판독은 대표 LOT 소속으로 보존
    visc = [r["lot_no"] for r in conn.execute(
        "SELECT lot_no FROM viscosity_readings ORDER BY id")]
    assert visc == ["잉크A26071203", "잉크A26071201"]
    # 감사 기록
    audits = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE action='product_lot_dedup'").fetchone()["n"]
    assert audits == 2
    # 정리 후 UNIQUE 봉인이 성립
    conn.execute("CREATE UNIQUE INDEX idx_blend_records_lot_unique ON blend_records(product_lot)")
    conn.close()


def test_app_startup_seals_unique_index():
    """create_app → init_db 마이그레이션 후 product_lot 에 유니크 인덱스가 존재한다."""
    from fastapi.testclient import TestClient
    from src.main import create_app

    TestClient(create_app())  # init_db 실행
    from src.db import get_connection
    with get_connection() as conn:
        idx = {r["name"]: r["unique"] for r in conn.execute(
            "PRAGMA index_list(blend_records)").fetchall()}
    assert idx.get("idx_blend_records_lot_unique") == 1
    assert "idx_blend_records_lot" not in idx
