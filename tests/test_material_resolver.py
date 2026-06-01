"""material_resolver 단위 테스트 - 자재명 정규화 및 ID 해석."""
import sqlite3

from src.services.material_resolver import (
    normalize_material_name,
    resolve_material,
    resolve_materials_bulk,
)


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE material_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            alias_name TEXT NOT NULL
        );
        """
    )
    return conn


class TestNormalizeMaterialName:
    def test_none_and_empty(self):
        assert normalize_material_name(None) == ""
        assert normalize_material_name("") == ""

    def test_upper_and_trim(self):
        assert normalize_material_name("  cyan  ") == "CYAN"

    def test_collapses_inner_whitespace(self):
        assert normalize_material_name("cyan   base") == "CYAN BASE"


class TestResolveMaterial:
    def test_by_name_case_insensitive(self):
        conn = _make_db()
        conn.execute("INSERT INTO materials (name) VALUES ('Cyan')")
        mid = conn.execute("SELECT id FROM materials WHERE name='Cyan'").fetchone()[0]
        assert resolve_material(conn, "  cyan ") == mid

    def test_inactive_not_matched(self):
        conn = _make_db()
        conn.execute("INSERT INTO materials (name, is_active) VALUES ('Cyan', 0)")
        assert resolve_material(conn, "Cyan") is None

    def test_by_alias(self):
        conn = _make_db()
        cur = conn.execute("INSERT INTO materials (name) VALUES ('Cyan')")
        mid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO material_aliases (material_id, alias_name) VALUES (?, 'CY')",
            (mid,),
        )
        assert resolve_material(conn, "cy") == mid

    def test_unknown_returns_none(self):
        conn = _make_db()
        assert resolve_material(conn, "Magenta") is None

    def test_empty_name_returns_none(self):
        conn = _make_db()
        assert resolve_material(conn, "  ") is None


class TestResolveMaterialsBulk:
    def test_batch(self):
        conn = _make_db()
        conn.execute("INSERT INTO materials (name) VALUES ('Cyan')")
        mid = conn.execute("SELECT id FROM materials WHERE name='Cyan'").fetchone()[0]
        result = resolve_materials_bulk(conn, ["Cyan", "Unknown"])
        assert result == {"Cyan": mid, "Unknown": None}
