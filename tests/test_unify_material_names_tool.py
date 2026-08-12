"""tools/unify_material_names.py — 과거 배합 기록의 자재 표기 통일.

이 도구는 완료된 기록을 직접 고친다. 그래서 "무엇을 바꾸지 않는가" 가 기능만큼 중요하다.
바꾸는 것은 자재명·자재연결(material_id)·기록 당시 자재코드 셋뿐이고, 작업일·작업시간·
작성시각·수량(이론량/실제량/비율)·자재 LOT·제품 LOT·작업자·수기입력/이월 플래그는
그대로여야 한다. 아래 test_apply_never_touches_amounts_or_dates 가 그걸 고정한다.

그 밖에 고정할 성질:
  · 매핑은 material_aliases(화면에서 사람이 등록한 동의어)에서 온다.
  · 마스터에도 동의어에도 없는 이름은 손대지 않는다.
  · 미리보기(기본)는 DB 를 전혀 바꾸지 않는다.
  · --apply 는 실행 직전 백업을 만들고, 무엇을 바꿨는지 audit_logs 에 남긴다.
  · 표준 라이브러리만 쓴다(운영 PC 단독 실행, src 패키지 비의존).
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

TOOL = Path("tools/unify_material_names.py")


def _load():
    spec = importlib.util.spec_from_file_location("unify_material_names", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_db(tmp_path) -> Path:
    """data/irms.db 배치 — 도구가 기대하는 구조(백업은 data 의 형제 backups/)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "irms.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT
        );
        CREATE TABLE material_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            alias_name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT, product_name TEXT, worker TEXT,
            work_date TEXT, work_time TEXT, total_amount REAL,
            status TEXT, created_at TEXT
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL,
            material_id INTEGER,
            material_code TEXT,
            material_name TEXT,
            material_lot TEXT,
            ratio REAL, theory_amount REAL, actual_amount REAL,
            sequence_order INTEGER, created_at TEXT,
            manual_entry INTEGER DEFAULT 0, carried_over INTEGER DEFAULT 0
        );
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor_user_id INTEGER, actor_username TEXT,
            actor_display_name TEXT, actor_access_level TEXT,
            target_type TEXT, target_id TEXT, target_label TEXT,
            details_json TEXT NOT NULL, created_at TEXT NOT NULL
        );

        INSERT INTO materials (name, code) VALUES ('PMA', 'AC0060');   -- id 1
        INSERT INTO materials (name, code) VALUES ('DF-2', 'B109');    -- id 2
        INSERT INTO materials (name, code) VALUES ('PB', 'B0020');     -- id 3
        INSERT INTO material_aliases (material_id, alias_name)
            VALUES (1, 'Propylene glycol monomethyl etheracetate');

        INSERT INTO blend_records
            (id, product_lot, product_name, worker, work_date, work_time,
             total_amount, status, created_at)
            VALUES (1, 'PB26070101', 'PB', '백정렬', '2026-07-01', '09:30',
                    1000, 'completed', '2026-07-01T09:35:00');

        -- (a) 동의어로만 아는 이름 + 코드 없음
        INSERT INTO blend_details
            (blend_record_id, material_id, material_code, material_name, material_lot,
             ratio, theory_amount, actual_amount, sequence_order, created_at, manual_entry)
            VALUES (1, NULL, '', 'Propylene glycol monomethyl etheracetate', 'LOT-A',
                    30.0, 300.0, 300.5, 1, '2026-07-01T09:31:00', 1);

        -- (b) 이름은 맞는데 남의 코드가 저장됨(PB 에 DF-2 의 B109)
        INSERT INTO blend_details
            (blend_record_id, material_id, material_code, material_name, material_lot,
             ratio, theory_amount, actual_amount, sequence_order, created_at, carried_over)
            VALUES (1, NULL, 'B109', 'PB', 'LOT-B',
                    70.0, 700.0, 699.5, 2, '2026-07-01T09:32:00', 1);

        -- (c) 마스터에도 동의어에도 없는 이름 — 손대면 안 된다
        INSERT INTO blend_details
            (blend_record_id, material_id, material_code, material_name, material_lot,
             ratio, theory_amount, actual_amount, sequence_order, created_at)
            VALUES (1, NULL, '', '정체불명자재', 'LOT-C',
                    0.0, 0.0, 12.5, 3, '2026-07-01T09:33:00');
        """
    )
    conn.commit()
    conn.close()
    return path


def _run(data_dir: Path, *args) -> subprocess.CompletedProcess:
    """도구를 별도 프로세스로 실행.

    자식 프로세스의 stdout 인코딩을 UTF-8 로 고정한다 — 그러지 않으면 Windows 에서
    콘솔 기본값(CP949)으로 인코딩돼 여기서 한글을 되읽지 못한다. 운영 PC 콘솔에서
    실제로 인코딩 가능한 문자만 쓰는지는 test_tools_console_encoding.py 가
    소스 수준에서 따로 지킨다(그쪽이 CP949 함정의 진짜 가드다).
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(TOOL), "--data-dir", str(data_dir), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )


def _rows(path: Path) -> dict[str, sqlite3.Row]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return {r["material_lot"]: r for r in conn.execute("SELECT * FROM blend_details")}
    finally:
        conn.close()


# ── 미리보기는 아무것도 바꾸지 않는다 ─────────────────────────────────────
def test_preview_changes_nothing(tmp_path):
    db = _make_db(tmp_path)
    before = {k: dict(v) for k, v in _rows(db).items()}

    res = _run(db.parent)
    assert res.returncode == 0, res.stderr
    assert "미리보기" in res.stdout

    after = {k: dict(v) for k, v in _rows(db).items()}
    assert before == after


# ── 핵심: 수량·날짜·LOT 은 절대 바뀌지 않는다 ────────────────────────────
def test_apply_never_touches_amounts_or_dates(tmp_path):
    """완료 기록을 고치는 도구라, '안 바뀌는 것' 을 기계로 고정한다."""
    db = _make_db(tmp_path)
    before = {k: dict(v) for k, v in _rows(db).items()}

    res = _run(db.parent, "--apply")
    assert res.returncode == 0, res.stderr

    after = _rows(db)
    keep = ("blend_record_id", "material_lot", "ratio", "theory_amount",
            "actual_amount", "sequence_order", "created_at",
            "manual_entry", "carried_over")
    for lot, old in before.items():
        for col in keep:
            assert after[lot][col] == old[col], f"{lot} 의 {col} 가 바뀌었다"

    # 배합 기록(헤더)도 그대로 — 작업일·작업시간·총량·작업자
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rec = conn.execute("SELECT * FROM blend_records WHERE id = 1").fetchone()
    conn.close()
    assert rec["work_date"] == "2026-07-01"
    assert rec["work_time"] == "09:30"
    assert rec["total_amount"] == 1000
    assert rec["worker"] == "백정렬"


# ── 바꿔야 하는 것은 바뀐다 ───────────────────────────────────────────────
def test_apply_unifies_name_code_and_link(tmp_path):
    db = _make_db(tmp_path)
    res = _run(db.parent, "--apply")
    assert res.returncode == 0, res.stderr
    after = _rows(db)

    # (a) 동의어 이름 → 마스터명 PMA, 코드·연결 채워짐
    a = after["LOT-A"]
    assert a["material_name"] == "PMA"
    assert a["material_code"] == "AC0060"
    assert a["material_id"] == 1

    # (b) 남의 코드(B109)가 PB 의 정상 코드로 정정됨
    b = after["LOT-B"]
    assert b["material_name"] == "PB"
    assert b["material_code"] == "B0020"
    assert b["material_id"] == 3


def test_unknown_material_name_is_left_alone(tmp_path):
    """마스터에도 동의어에도 없는 이름은 건드리지 않는다(임의 자재 생성 금지)."""
    db = _make_db(tmp_path)
    _run(db.parent, "--apply")
    c = _rows(db)["LOT-C"]
    assert c["material_name"] == "정체불명자재"
    assert c["material_id"] is None
    assert c["material_code"] == ""

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
    conn.close()
    assert n == 3, "자재가 새로 생겼다"


# ── 백업과 감사 로그 ──────────────────────────────────────────────────────
def test_apply_makes_backup_and_audit(tmp_path):
    db = _make_db(tmp_path)
    res = _run(db.parent, "--apply", "--actor", "홍책임")
    assert res.returncode == 0, res.stderr

    backups = list((tmp_path / "backups").glob("irms_before_unify_*.db"))
    assert len(backups) == 1, "실행 직전 백업이 없다"
    # 백업은 바꾸기 전 상태여야 한다.
    bconn = sqlite3.connect(backups[0])
    old_name = bconn.execute(
        "SELECT material_name FROM blend_details WHERE material_lot = 'LOT-A'"
    ).fetchone()[0]
    bconn.close()
    assert old_name == "Propylene glycol monomethyl etheracetate"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    log = conn.execute(
        "SELECT * FROM audit_logs WHERE action = 'material_names_unified'"
    ).fetchone()
    conn.close()
    assert log is not None, "감사 로그가 없다"
    assert log["actor_username"] == "홍책임"
    details = json.loads(log["details_json"])
    assert details["changed_rows"] == 2
    assert any(g["from"] == "Propylene glycol monomethyl etheracetate" for g in details["groups"])


def test_second_run_finds_nothing(tmp_path):
    """한 번 정리하면 두 번째 실행은 바꿀 것이 없다(멱등)."""
    db = _make_db(tmp_path)
    _run(db.parent, "--apply")
    res = _run(db.parent)
    assert "바꿀 것이 없습니다" in res.stdout


# ── 그때는 맞았던 저장코드는 남긴다 ──────────────────────────────────────
def _add_row_with_old_code(db: Path, code: str) -> None:
    """폐기된(그러나 당시엔 정확했던) 코드로 기록된 행 하나 추가."""
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO materials (name, code) VALUES ('PVP K90', 'AW0027')")
    conn.execute(
        "INSERT INTO blend_details "
        "(blend_record_id, material_id, material_code, material_name, material_lot,"
        " ratio, theory_amount, actual_amount, sequence_order, created_at) "
        "VALUES (1, NULL, ?, 'PVP K90', 'LOT-OLD', 0.0, 0.0, 380.0, 4, '2025-06-26T10:00:00')",
        (code,),
    )
    conn.commit()
    conn.close()


def test_default_keep_list_protects_a_historically_correct_code(tmp_path):
    """KEEP_STORED_CODES 의 코드는 정정하지 않는다 — 자재 연결만 채운다.

    운영자가 --keep-code 를 빼먹어도 지켜져야 하므로 기본값에 들어 있다.
    """
    m = _load()
    assert "AS0066" in m.KEEP_STORED_CODES, "기본 보존 목록이 비었다"

    db = _make_db(tmp_path)
    _add_row_with_old_code(db, "AS0066")

    res = _run(db.parent, "--apply")
    assert res.returncode == 0, res.stderr

    row = _rows(db)["LOT-OLD"]
    assert row["material_code"] == "AS0066", "그 당시 코드가 덮어써졌다"
    assert row["material_name"] == "PVP K90"
    assert row["material_id"] is not None, "자재 연결은 채워져야 한다"


def test_keep_code_option_adds_to_the_default_list(tmp_path):
    """--keep-code 로 추가 지정할 수 있다(대소문자 무시)."""
    db = _make_db(tmp_path)
    _add_row_with_old_code(db, "AX9999")

    res = _run(db.parent, "--apply", "--keep-code", "ax9999")
    assert res.returncode == 0, res.stderr
    assert _rows(db)["LOT-OLD"]["material_code"] == "AX9999"


def test_without_keep_the_same_code_would_be_corrected(tmp_path):
    """대조군 — 보존 목록에 없는 폐기 코드는 마스터 코드로 정정된다."""
    db = _make_db(tmp_path)
    _add_row_with_old_code(db, "AX9999")

    res = _run(db.parent, "--apply")
    assert res.returncode == 0, res.stderr
    assert _rows(db)["LOT-OLD"]["material_code"] == "AW0027"


def test_preview_names_the_protected_rows(tmp_path):
    """보존 대상은 변경 목록에서 빠지므로, 손대지 않는다는 사실을 따로 알린다."""
    db = _make_db(tmp_path)
    _add_row_with_old_code(db, "AS0066")

    res = _run(db.parent)
    assert "손대지 않는 저장코드" in res.stdout
    assert "AS0066" in res.stdout


# ── 매칭 규칙 ─────────────────────────────────────────────────────────────
def test_norm_ignores_case_space_and_symbols():
    m = _load()
    assert m._norm("GMMA (Evonik)") == m._norm("gmma(evonik)")
    assert m._norm("  L-HEMA ") == m._norm("lhema")
    assert m._norm("---") == ""


def test_tool_does_not_import_src_package():
    """운영 PC 단독 실행 — src 패키지에 의존하지 않는다."""
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src"):
            raise AssertionError(f"src 임포트: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src"), f"src 임포트: {alias.name}"


def test_update_statement_touches_only_three_columns():
    """UPDATE 문에 수량·일시 컬럼이 등장하지 않는다는 것을 소스로 고정.

    테스트가 데이터로 확인하는 것과 별개로, 나중에 누가 SET 절에 컬럼을 더해도
    여기서 걸리게 한다.
    """
    source = TOOL.read_text(encoding="utf-8")
    set_clause = "SET material_name = ?, material_id = ?, material_code = ?"
    assert set_clause in source, "UPDATE SET 절이 바뀌었다 — 범위를 다시 검토할 것"
    for forbidden in ("actual_amount =", "theory_amount =", "ratio =",
                      "work_date =", "work_time =", "material_lot ="):
        assert forbidden not in source, f"금지 컬럼이 대입된다: {forbidden}"
