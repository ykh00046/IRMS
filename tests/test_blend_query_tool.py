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
import os
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
            -- 실제 스키마에 있는 열 — blend_service.mistake_stats 가 자재별 수동 입력을
            -- 셀 때 쓴다(도구와 앱을 대조하는 테스트가 analysis 를 부르므로 필요).
            manual_entry INTEGER NOT NULL DEFAULT 0,
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
        "urllib",   # 실시간 조회(운영 서버 GET) — 표준 라이브러리만으로 붙인다
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


# ── 도구와 화면이 같은 숫자를 말하는가 ────────────────────────────────────
# 도구가 화면과 다른 답을 주면 둘 중 뭘 믿어야 할지 알 수 없다. 실제로 어긋난 적이
# 있다(2026-08-10: 화면 77.4% · 도구 98.7% — 도구가 저울 도입일 설정을 몰랐다).

def _with_scale_since(db: Path, day: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_settings ("
        " key TEXT PRIMARY KEY, value TEXT, updated_by TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('scale_since', ?)",
        (day,),
    )
    conn.commit()
    conn.close()


def test_scale_rate_honours_the_adoption_date(tmp_path):
    db = _make_db(tmp_path)
    _with_scale_since(db, "2026-07-02")   # 7/1 배합은 표본에서 빠진다
    body = _run(tmp_path, "summary")
    row = body["rows"][0]
    assert row["계량률_기준일"] == "2026-07-02"
    assert row["계량률_표본"] == 1        # 7/2 의 1건(수동)만
    assert row["저울계량률_%"] == 0.0
    # 건수·생산량은 좁히지 않는다 — 분모만 좁힌다.
    assert row["배합건수"] == 2


def test_scale_rate_says_when_no_basis_is_set(tmp_path):
    """기준이 없으면 숫자를 그냥 내놓지 않고 '미설정'이라고 밝힌다."""
    _make_db(tmp_path)
    row = _run(tmp_path, "summary")["rows"][0]
    assert "미설정" in row["계량률_기준일"]
    assert row["계량률_표본"] == 2


def test_tool_matches_the_analysis_service(tmp_path):
    """같은 DB·같은 기준이면 도구와 blend_service.analysis 가 같은 값을 낸다."""
    db = _make_db(tmp_path)
    _with_scale_since(db, "2026-07-02")

    import sqlite3 as _s
    from src.services import blend_service as bs

    conn = _s.connect(db)
    conn.row_factory = _s.Row
    try:
        app = bs.analysis(conn, None, None)["summary"]
    finally:
        conn.close()

    tool = _run(tmp_path, "summary")["rows"][0]
    assert tool["저울계량률_%"] == app["scale_rate"]
    assert tool["계량률_표본"] == app["scale_base_records"]
    assert tool["배합건수"] == app["records"]


def test_empty_database_is_flagged_not_reported_as_zero(tmp_path):
    """빈 DB 의 '0건'을 진짜 답으로 읽지 않게 한다.

    개발 PC 에는 배합 기록 0건짜리 data/irms.db 가 있어, --db 를 빼면 오류 없이
    0건이라고 답한다. 어느 DB 를 읽었는지와 기록 유무를 함께 밝힌다(2026-08-10).
    """
    empty = tmp_path / "irms.db"
    conn = sqlite3.connect(empty)
    conn.executescript(
        "CREATE TABLE blend_records (id INTEGER PRIMARY KEY, product_lot TEXT,"
        " product_name TEXT, worker TEXT, work_date TEXT, total_amount REAL,"
        " status TEXT DEFAULT 'completed', manual_entry INTEGER DEFAULT 0,"
        " rescale_count INTEGER DEFAULT 0, oversize_total INTEGER DEFAULT 0,"
        " is_bulk_regenerated INTEGER DEFAULT 0);"
        "CREATE TABLE blend_details (id INTEGER PRIMARY KEY, blend_record_id INTEGER,"
        " material_name TEXT, actual_amount REAL, theory_amount REAL, loss_comp_g REAL DEFAULT 0);"
    )
    conn.commit()
    conn.close()

    proc = subprocess.run(
        [sys.executable, str(TOOL), "summary", "--db", str(empty)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    assert "[스냅샷]" in proc.stdout          # 어느 파일을 읽었는지
    assert "배합 기록이 없습니다" in proc.stdout

    body = json.loads(subprocess.run(
        [sys.executable, str(TOOL), "summary", "--db", str(empty), "--json"],
        capture_output=True, text=True, encoding="utf-8",
    ).stdout)
    assert body["database_has_records"] is False


def test_populated_database_reports_records_present(tmp_path):
    body = _run(tmp_path, "summary")
    assert body["database_has_records"] is True


# ── 경로를 매번 지정하지 않아도 되는가 ─────────────────────────────────────
# 스냅샷 파일명에는 시각이 박혀 있어(irms_20260810_130412.db) 새 사본이 올 때마다
# 경로가 바뀐다. 그때마다 명령을 고쳐 쓰게 두지 않는다(2026-08-10).

def test_picks_the_newest_snapshot_without_being_told(tmp_path, monkeypatch):
    import os
    import time

    module = _load()
    root = tmp_path / "repo"
    (root / "local-data").mkdir(parents=True)
    old = root / "local-data" / "irms_20260101_000000.db"
    new = root / "local-data" / "irms_20260810_130412.db"
    old.write_bytes(b"")
    new.write_bytes(b"")
    past = time.time() - 86400
    os.utime(old, (past, past))

    monkeypatch.chdir(root)
    monkeypatch.delenv("IRMS_QUERY_DB", raising=False)
    monkeypatch.delenv("IRMS_DATA_DIR", raising=False)
    assert module.resolve_db(None).name == new.name


def test_explicit_db_always_wins(tmp_path, monkeypatch):
    module = _load()
    root = tmp_path / "repo"
    (root / "local-data").mkdir(parents=True)
    (root / "local-data" / "irms_snap.db").write_bytes(b"")
    monkeypatch.chdir(root)
    assert module.resolve_db("chosen.db") == Path("chosen.db")


def test_env_var_can_point_at_a_folder(tmp_path, monkeypatch):
    """백업 미러 폴더를 그대로 가리켜도 그 안의 최신 파일을 집는다."""
    import os
    import time

    module = _load()
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    older = mirror / "irms_a.db"
    newer = mirror / "irms_b.db"
    older.write_bytes(b"")
    newer.write_bytes(b"")
    past = time.time() - 86400
    os.utime(older, (past, past))

    monkeypatch.setenv("IRMS_QUERY_DB", str(mirror))
    assert module.resolve_db(None) == newer


# ── 실시간 조회(운영 서버) ────────────────────────────────────────────────
#
# 실시간 경로는 네트워크가 있어야 도는 코드다. 그래서 응답 → 표 변환을 순수 함수로
# 떼어 두고, 저장된 응답만으로 검증한다. 검증할 수 없는 경로가 기본값이 되는 게
# 제일 위험하다.

ANALYSIS_PAYLOAD = {
    "range": {"start": "2026-07-01", "end": "2026-07-31", "days": 31},
    "scale_since": "2026-07-01",
    "bucket": "month",
    "summary": {
        "records": 144, "total_weight_g": 1323207.0, "product_count": 19,
        "material_count": 56, "manual_records": 19, "canceled_records": 0,
        "rescale_records": 1, "oversize_records": 0, "loss_comp_total_g": 0.0,
        "scale_rate": 86.8, "scale_base_records": 144, "cancel_rate": 0.0,
    },
    "trend": [
        {"bucket": "2026-06", "records": 185, "weight_g": 1837220.0, "manual_records": 0,
         "canceled_records": 0, "scale_rate": None, "scale_base_records": 0, "partial": False},
        {"bucket": "2026-07", "records": 144, "weight_g": 1323207.0, "manual_records": 19,
         "canceled_records": 0, "scale_rate": 86.8, "scale_base_records": 144, "partial": True},
    ],
    "products": [{"product_name": "APB", "batch_count": 20, "total_amount": 337059.7,
                  "share": 25.5, "last_work_date": "2026-07-30"}],
    "materials": [{"material_name": "PB", "total_actual": 337059.7, "total_theory": 337000.0,
                   "usage_count": 20, "share": 25.5, "loss_comp_g": 0.0}],
    "workers": [{"worker": "강민수", "records": 36, "total_amount": 240000.0, "product_count": 4}],
    "quality": {
        "by_worker": [{"worker": "강민수", "records": 36, "manual_records": 9,
                       "canceled_records": 0, "manual_rate": 25.0}],
        "by_material": [],
    },
}


class _Args:
    """명령줄 인자 대역 — 순수 변환 함수에 넘길 최소 형태."""

    def __init__(self, **kw):
        self.from_date = self.to_date = self.product = self.worker = None
        self.material = self.lot = None
        self.limit = 200
        self.command = "summary"
        for key, value in kw.items():
            setattr(self, key, value)


def test_live_summary_reports_the_servers_own_numbers():
    """도구가 제 SQL 로 다시 계산하지 않고 서버 값을 그대로 옮기는가."""
    module = _load()
    row = module.shape_summary(ANALYSIS_PAYLOAD, _Args())[0]
    assert row["배합건수"] == 144
    assert row["총생산량_kg"] == 1323.207
    assert row["수동입력"] == 19
    assert row["저울계량률_%"] == 86.8
    assert row["계량률_표본"] == 144
    assert row["계량률_기준일"] == "2026-07-01"
    assert row["조회기간"] == "2026-07-01 ~ 2026-07-31"


def test_live_and_snapshot_use_the_same_metric_names(tmp_path):
    """같은 값을 두 경로가 다른 이름으로 부르면 두 답을 비교할 수 없다."""
    module = _load()
    live = module.shape_summary(ANALYSIS_PAYLOAD, _Args())[0]
    snapshot = _run(tmp_path, "summary")["rows"][0]
    shared = {"배합건수", "총생산량_kg", "제품종수", "수동입력", "취소",
              "증량적용", "상한초과", "저울계량률_%", "계량률_표본", "계량률_기준일"}
    assert shared <= set(live), f"실시간에 없는 이름: {sorted(shared - set(live))}"
    assert shared <= set(snapshot), f"스냅샷에 없는 이름: {sorted(shared - set(snapshot))}"


def test_live_summary_without_a_range_says_전체_not_none():
    module = _load()
    payload = {**ANALYSIS_PAYLOAD, "range": {"start": None, "end": None, "days": None}}
    assert module.shape_summary(payload, _Args())[0]["조회기간"] == "전체"


def test_live_records_omit_the_manual_column_instead_of_showing_a_false_zero():
    """서버는 책임자에게만 수동 입력 표시를 준다(_mask_manual_entry).

    자격 없이 받은 값은 전부 0 이므로, 그대로 '수동' 열에 실으면 '수동 입력 0건'
    이라는 거짓말이 된다. 열 자체를 내지 않아야 한다.
    """
    module = _load()
    payload = {"items": [{
        "work_date": "2026-08-10", "product_lot": "APB26081001", "product_name": "APB",
        "worker": "박종휘", "total_amount": 17561.2, "status": "completed",
        "manual_entry": False, "manual_absence_reason": None, "reactor": None,
    }]}
    row = module.shape_records(payload, _Args())[0]
    assert "수동" not in row
    assert not any("수동" in key for key in row)
    assert row["제품LOT"] == "APB26081001"


def test_manual_and_sql_are_answered_by_the_snapshot_with_a_reason():
    """서버로 못 답하는 명령은 조용히 빈 답을 주지 말고 이유를 밝히고 파일로 간다."""
    module = _load()
    for command in ("manual", "rescale", "sql", "schema"):
        reason = module.live_blocked_reason(_Args(command=command))
        assert reason, f"{command}: 이유 없이 막혔다"
    assert module.live_blocked_reason(_Args(command="summary")) is None
    # 자재별 월 소비를 주는 읽기 경로는 서버에 없다.
    assert module.live_blocked_reason(_Args(command="monthly", material="PB"))
    assert module.live_blocked_reason(_Args(command="monthly")) is None


def test_live_viscosity_returns_the_most_recent_readings_first():
    """서버는 오래된 순으로 준다 — 그대로 자르면 2024 년 값으로 '요즘 점도'를 답한다."""
    module = _load()
    payload = {
        "product": {"code": "APB", "target": None, "lower_limit": None, "upper_limit": None},
        "readings": [
            {"measured_date": "2024-08-30", "lot_no": "APB24082802", "viscosity": 363.0,
             "status": "normal", "side": None, "excluded": 0},
            {"measured_date": "2026-07-30", "lot_no": "APB26040901", "viscosity": 400.2,
             "status": "normal", "side": None, "excluded": 0},
            {"measured_date": "2026-07-24", "lot_no": "APB26071502", "viscosity": 128.4,
             "status": "anomaly", "side": "low", "excluded": 0},
        ],
    }
    rows = module.shape_viscosity_readings(payload, _Args(limit=2))
    assert [r["측정일"] for r in rows] == ["2026-07-30", "2026-07-24"]
    assert rows[1]["판정"] == "이상(low)"


def test_live_monthly_marks_the_unfinished_bucket():
    """진행 중인 달을 끝난 달과 나란히 놓으면 '생산이 줄었다'로 읽힌다."""
    module = _load()
    rows = module.shape_monthly(ANALYSIS_PAYLOAD, _Args())
    assert rows[0]["진행중"] == ""
    assert rows[1]["진행중"] == "진행중"
    # 저울 도입 전 구간은 0% 가 아니라 '말할 수 없음'이다.
    assert rows[0]["저울계량률_%"] is None and rows[0]["계량률_표본"] == 0


def test_db_flag_and_offline_never_touch_the_network(tmp_path):
    """--db / --offline 을 준 사람은 그 파일을 보겠다는 뜻이다. 서버를 부르면 안 된다.

    IRMS_API_URL 을 닿지 않는 주소로 두고 부른다 — 서버를 보러 갔다면 물러났다는
    note 가 붙거나 느려질 텐데, 아예 시도하지 않아야 한다.
    """
    db = _make_db(tmp_path)
    for extra in (["--db", str(db)], ["--offline", "--db", str(db)]):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "summary", *extra, "--json"],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "IRMS_API_URL": "http://127.0.0.1:1"},
        )
        assert proc.returncode == 0, proc.stderr
        body = json.loads(proc.stdout)
        assert body["source"] == "snapshot"
        assert body["note"] is None, "서버를 보러 갔다가 물러난 흔적이 있다"


def test_offline_env_var_forces_the_snapshot(tmp_path):
    """자동화·CI 에서 실수로 운영 서버를 두드리지 않게 막는 스위치."""
    db = _make_db(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(TOOL), "summary", "--db", str(db), "--json"],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "IRMS_QUERY_OFFLINE": "1"},
    )
    body = json.loads(proc.stdout)
    assert body["source"] == "snapshot" and body["origin"].endswith("irms.db")


def test_catalog_says_which_commands_answer_live():
    """부르는 쪽이 사본을 준비해야 하는지 미리 알 수 있어야 한다."""
    module = _load()
    rows = module.q_catalog(None, None)
    by_name = {r["command"]: r["조회원본"] for r in rows}
    assert by_name["summary"] == "실시간"
    assert by_name["sql"] == "스냅샷 파일"
    assert by_name["catalog"] == "DB 불필요"
    assert set(by_name) == set(module.COMMANDS)


def test_live_workers_warn_when_manual_columns_are_masked():
    """빈 칸을 '수동 입력 0건'으로 읽으면 거짓말이 된다 — 왜 비었는지 말해야 한다."""
    module = _load()
    module.WARNINGS.clear()
    masked = {
        "workers": [{"worker": "김철수", "records": 3, "total_amount": 3000.0,
                     "product_count": 1}],
        "quality": {
            "manual_visible": False,
            "by_worker": [{"worker": "김철수", "records": 3, "manual_records": None,
                           "canceled_records": 0, "manual_rate": None}],
        },
    }
    rows = module.shape_workers(masked, _Args())
    assert rows[0]["수동입력"] is None and rows[0]["수동비율_퍼센트"] is None
    assert any("책임자" in w for w in module.WARNINGS), "가려진 이유를 말하지 않는다"

    module.WARNINGS.clear()
    module.shape_workers(ANALYSIS_PAYLOAD, _Args())   # manual_visible 없음 = 책임자
    assert not module.WARNINGS
