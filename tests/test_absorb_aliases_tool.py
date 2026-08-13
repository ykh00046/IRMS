"""tools/absorb_aliases.py — 동의어 일괄 흡수·삭제 도구 검증.

동의어별로 (1) 그 표기의 기록을 정본 이름으로 통합·FK 연결, (2) 동의어 삭제.
코드값 동의어(기록 없음)는 삭제만, 충돌(다른 자재의 이름/동의어와 같은 키)은
보류하는지, 미리보기가 무변경인지 확인한다. 픽스처는 tmp_path 의 최소 스키마.
"""

from __future__ import annotations

import sqlite3

from tools.absorb_aliases import apply_plan, build_plan, main


SCHEMA = """
CREATE TABLE materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    code TEXT
);
CREATE TABLE material_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
    alias_name TEXT NOT NULL UNIQUE
);
CREATE TABLE blend_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER REFERENCES materials(id),
    material_name TEXT NOT NULL,
    material_code TEXT
);
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT,
    actor_username TEXT,
    actor_display_name TEXT,
    target_type TEXT,
    target_label TEXT,
    details_json TEXT,
    created_at TEXT
);
"""


def _make_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _mat(conn, name, code=None):
    conn.execute("INSERT INTO materials (name, code) VALUES (?, ?)", (name, code))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _alias(conn, mid, name):
    conn.execute(
        "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, ?)",
        (mid, name),
    )


def _detail(conn, name, mid=None):
    conn.execute(
        "INSERT INTO blend_details (material_id, material_name, material_code) "
        "VALUES (?, ?, '')",
        (mid, name),
    )


def test_variant_alias_absorbs_records_and_code_alias_just_deletes(tmp_path):
    conn = _make_db(tmp_path / "a.db")
    mp = _mat(conn, "MP", "AC0045")
    _alias(conn, mp, "MEHQ")        # 변형 표기 - 기록 2행
    _alias(conn, mp, "AC0045")      # 코드값 동의어 - 기록 없음
    _detail(conn, "MEHQ")
    _detail(conn, "MEHQ")
    _detail(conn, "MP", mid=mp)     # 정본 표기 행은 손대지 않는다
    conn.commit()

    plan, conflicts = build_plan(conn)
    assert conflicts == []
    by_alias = {p["alias_name"]: p for p in plan}
    assert by_alias["MEHQ"]["rows"] == 2
    assert by_alias["AC0045"]["rows"] == 0

    changed = apply_plan(conn, plan, actor="테스트", backup_name="b.db")
    conn.commit()
    assert changed == 2
    rows = conn.execute(
        "SELECT material_name, material_id FROM blend_details ORDER BY id"
    ).fetchall()
    assert [(r["material_name"], r["material_id"]) for r in rows] == [
        ("MP", mp), ("MP", mp), ("MP", mp),
    ]
    assert conn.execute("SELECT COUNT(*) FROM material_aliases").fetchone()[0] == 0
    audit = conn.execute("SELECT action FROM audit_logs").fetchall()
    assert [a["action"] for a in audit] == ["aliases_absorbed"]


def test_records_linked_to_other_material_are_not_taken(tmp_path):
    """다른 자재에 FK 연결된 행은 이름이 같아도 빼앗지 않는다."""
    conn = _make_db(tmp_path / "b.db")
    a = _mat(conn, "PMA", "AC0060")
    b = _mat(conn, "DF-2", "B109")
    _alias(conn, a, "긴화학명")
    _detail(conn, "긴화학명")            # NULL FK -> 흡수 대상
    _detail(conn, "긴화학명", mid=b)     # 남의 행 -> 불가침
    conn.commit()

    plan, _ = build_plan(conn)
    assert plan[0]["rows"] == 1
    apply_plan(conn, plan, actor="t", backup_name="x")
    conn.commit()
    other = conn.execute(
        "SELECT material_name, material_id FROM blend_details WHERE material_id = ?",
        (b,),
    ).fetchall()
    assert [(r["material_name"], r["material_id"]) for r in other] == [("긴화학명", b)]


def test_conflicting_aliases_are_held_for_human_judgment(tmp_path):
    conn = _make_db(tmp_path / "c.db")
    a = _mat(conn, "AAA")
    b = _mat(conn, "BBB")
    _alias(conn, a, "BBB ")          # 다른 자재의 이름과 같은 키 -> 보류
    _alias(conn, a, "공용표기")       # 서로 다른 자재의 동의어 -> 둘 다 보류
    _alias(conn, b, "공용 표기")
    conn.commit()

    plan, conflicts = build_plan(conn)
    assert plan == []
    assert len(conflicts) == 3
    reasons = " / ".join(c["reason"] for c in conflicts)
    assert "이름과 같음" in reasons
    assert "여러 자재의 동의어" in reasons


def test_preview_changes_nothing(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = _make_db(data_dir / "irms.db")
    mp = _mat(conn, "MP", "AC0045")
    _alias(conn, mp, "MEHQ")
    _detail(conn, "MEHQ")
    conn.commit()
    conn.close()

    rc = main(["--data-dir", str(data_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "미리보기" in out

    conn = sqlite3.connect(data_dir / "irms.db")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM material_aliases").fetchone()[0] == 1
    assert conn.execute(
        "SELECT material_name FROM blend_details"
    ).fetchone()["material_name"] == "MEHQ"
    conn.close()


def test_apply_via_main_creates_backup_and_absorbs(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = _make_db(data_dir / "irms.db")
    mp = _mat(conn, "MP", "AC0045")
    _alias(conn, mp, "MEHQ")
    _detail(conn, "MEHQ")
    conn.commit()
    conn.close()

    rc = main(["--data-dir", str(data_dir), "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "완료" in out
    assert list((tmp_path / "backups").glob("irms_before_absorb_*.db"))

    conn = sqlite3.connect(data_dir / "irms.db")
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM material_aliases").fetchone()[0] == 0
    assert conn.execute(
        "SELECT material_name FROM blend_details"
    ).fetchone()["material_name"] == "MP"
    conn.close()
