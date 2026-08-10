"""tools/blend_query.py — 읽기 전용 조회 도구 검증.

이 도구의 쓸모는 두 가지 성질에 걸려 있다:
  ① 운영 DB 를 절대 바꾸지 않는다 — 그래야 운영 PC 에서 마음 놓고 돌린다.
  ② 표준 라이브러리만 쓴다 — 프로젝트 venv 밖(다른 에이전트·다른 PC)에서도 돌아야 한다.
둘 다 코드 리뷰가 아니라 테스트로 고정한다.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

TOOL = Path("tools/blend_query.py")


def _load():
    spec = importlib.util.spec_from_file_location("blend_query", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_db(tmp_path) -> Path:
    path = tmp_path / "irms.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL, product_name TEXT NOT NULL, worker TEXT NOT NULL,
            work_date TEXT NOT NULL, work_time TEXT, total_amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            manual_entry INTEGER NOT NULL DEFAULT 0,
            manual_unacked INTEGER NOT NULL DEFAULT 0,
            manual_absence_reason TEXT,
            rescale_count INTEGER NOT NULL DEFAULT 0,
            rescale_unacked INTEGER NOT NULL DEFAULT 0,
            oversize_total INTEGER NOT NULL DEFAULT 0,
            is_bulk_regenerated INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT, blend_record_id INTEGER NOT NULL,
            material_name TEXT NOT NULL, material_lot TEXT, ratio REAL,
            theory_amount REAL, actual_amount REAL, sequence_order INTEGER DEFAULT 0,
            loss_comp_g REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE viscosity_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE, name TEXT,
            target REAL, lower_limit REAL, upper_limit REAL
        );
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            lot_no TEXT NOT NULL, viscosity REAL NOT NULL, measured_date TEXT,
            excluded INTEGER NOT NULL DEFAULT 0, blend_record_id INTEGER
        );
        INSERT INTO viscosity_products (code, name, target, lower_limit, upper_limit)
            VALUES ('PB', 'PB', 50, 45, 55);
        """
    )
    # 완료 2건 + 취소 1건 + 일괄 재생성 1건(집계에서 빠져야 한다)
    rows = [
        ("APB-1", "APB", "김철수", "2026-07-01", 1000.0, "completed", 0, 0),
        ("APB-2", "APB", "이영희", "2026-07-02", 2000.0, "completed", 1, 1),
        ("APB-3", "APB", "김철수", "2026-07-03", 9999.0, "canceled", 0, 0),
        ("APB-4", "APB", "김철수", "2026-07-04", 8888.0, "completed", 0, 0),
    ]
    for i, (lot, name, worker, day, total, status, manual, resc) in enumerate(rows):
        bulk = 1 if lot == "APB-4" else 0
        rid = conn.execute(
            "INSERT INTO blend_records (product_lot, product_name, worker, work_date,"
            " total_amount, status, manual_entry, rescale_count, is_bulk_regenerated)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (lot, name, worker, day, total, status, manual, resc, bulk),
        ).lastrowid
        conn.execute(
            "INSERT INTO blend_details (blend_record_id, material_name, material_lot,"
            " theory_amount, actual_amount, loss_comp_g) VALUES (?,?,?,?,?,?)",
            (rid, "PB", f"RM-260{i}", total * 0.6, total * 0.6, 1.0),
        )
    conn.execute(
        "INSERT INTO viscosity_readings (product_id, lot_no, viscosity, measured_date,"
        " blend_record_id) VALUES (1, 'APB-1', 49.5, '2026-07-01', 1)"
    )
    conn.commit()
    conn.close()
    return path


def _run(tmp_path, *args) -> dict:
    db = _make_db(tmp_path) if not (tmp_path / "irms.db").exists() else tmp_path / "irms.db"
    proc = subprocess.run(
        [sys.executable, str(TOOL), *args, "--db", str(db), "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# ── ① 읽기 전용 ────────────────────────────────────────────────────────────

def test_connection_cannot_write(tmp_path):
    """mode=ro 로 열어 어떤 경로로도 DB 를 바꿀 수 없다."""
    module = _load()
    conn = module.connect(_make_db(tmp_path))
    try:
        with __import__("pytest").raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM blend_records")
    finally:
        conn.close()


def test_free_sql_rejects_writes_and_multiple_statements(tmp_path):
    db = _make_db(tmp_path)
    for statement in (
        "DELETE FROM blend_records",
        "UPDATE blend_records SET total_amount = 0",
        "DROP TABLE blend_records",
        "SELECT 1; DROP TABLE blend_records",
        "PRAGMA table_info(blend_records)",
    ):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "sql", statement, "--db", str(db)],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode != 0, f"허용되면 안 되는 문장이 통과했다: {statement}"


def test_free_sql_allows_reads(tmp_path):
    body = _run(tmp_path, "sql", "SELECT COUNT(*) AS n FROM blend_records")
    assert body["rows"][0]["n"] == 4


# ── ② 표준 라이브러리만 (프로젝트 밖에서도 돈다) ───────────────────────────

def test_tool_imports_only_stdlib():
    """프로젝트 모듈이나 서드파티를 쓰면 다른 환경(에이전트·다른 PC)에서 못 돌린다."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    allowed = {
        "__future__", "argparse", "json", "os", "re", "sqlite3", "sys", "pathlib",
    }
    assert imported <= allowed, f"표준 라이브러리 밖 의존: {sorted(imported - allowed)}"


# ── 집계 정의가 화면과 같은가 ──────────────────────────────────────────────

def test_summary_counts_only_completed_non_bulk(tmp_path):
    body = _run(tmp_path, "summary")
    row = body["rows"][0]
    assert row["배합건수"] == 2          # 취소 1 · 일괄 재생성 1 제외
    assert row["총생산량_kg"] == 3.0     # 1000 + 2000
    assert row["취소"] == 1
    assert row["수동입력"] == 1
    assert row["저울계량률_%"] == 50.0


def test_material_lot_trace_ignores_the_period(tmp_path):
    body = _run(tmp_path, "material-lot", "RM-260")
    assert body["row_count"] >= 2
    assert all("RM-260" in r["자재LOT"] for r in body["rows"])


def test_viscosity_joins_the_linked_batch(tmp_path):
    body = _run(tmp_path, "viscosity")
    assert body["rows"][0]["연계배합"] == "APB-1"
    assert body["rows"][0]["판정"] == ""      # 45~55 안


def test_json_envelope_carries_the_question(tmp_path):
    """결과만 보면 어떤 질문의 답인지 알 수 없다 — 조건을 함께 싣는다."""
    body = _run(tmp_path, "records", "--worker", "김철수")
    assert body["command"] == "records"
    assert body["filters"]["worker"] == "김철수"
    assert body["row_count"] == len(body["rows"])


def test_catalog_answers_without_a_database(tmp_path):
    """에이전트가 접속 전에 먼저 물어보는 명령 — DB 가 없어도 답해야 한다."""
    proc = subprocess.run(
        [sys.executable, str(TOOL), "catalog", "--db", str(tmp_path / "없는파일.db"), "--json"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    body = json.loads(proc.stdout)
    names = {r["command"] for r in body["rows"]}
    module = _load()
    # 카탈로그와 실제 명령 목록이 어긋나면 에이전트가 없는 명령을 부른다.
    assert names == set(module.COMMANDS)


def test_unknown_option_is_an_error_not_swallowed(tmp_path):
    db = _make_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "records", "--prodct", "APB", "--db", str(db)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode != 0
