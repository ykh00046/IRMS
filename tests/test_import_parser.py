"""import_parser 단위 테스트 - 붙여넣기 텍스트 → 레시피 파싱.

순수 헬퍼 `_parse_value`는 직접, `parse_import_text`는 in-memory SQLite로 검증한다.
"""
import sqlite3

from src.services.import_parser import _parse_value, parse_import_text


class TestParseValue:
    def test_dash_is_skip(self):
        # '-'는 placeholder가 아니라 건너뛰기((None, None))로 처리
        assert _parse_value("-") == (None, None)

    def test_empty_and_none(self):
        assert _parse_value("") == (None, None)
        assert _parse_value(None) == (None, None)

    def test_strips_thousands_comma(self):
        assert _parse_value("1,234") == (1234.0, None)

    def test_plain_number(self):
        assert _parse_value("12.5") == (12.5, None)

    def test_mixed_text(self):
        assert _parse_value("12.5 (HR10)") == (12.5, "(HR10)")


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit_type TEXT NOT NULL DEFAULT 'weight',
            unit TEXT NOT NULL DEFAULT 'g',
            color_group TEXT NOT NULL DEFAULT 'none',
            category TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            code TEXT
        );
        CREATE TABLE material_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            alias_name TEXT NOT NULL
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER,
            material_id INTEGER NOT NULL
        );
        -- item-code P1: 파서가 item_code_master 를 조회한다(비어 있으면 하위호환 모드).
        CREATE TABLE item_code_master (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            spec TEXT,
            unit TEXT,
            kind TEXT NOT NULL,
            category_hint TEXT,
            source TEXT,
            imported_at TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO materials (name) VALUES ('Cyan')")
    conn.execute("INSERT INTO materials (name) VALUES ('Yellow')")
    return conn


class TestParseImportText:
    def test_empty_input_errors(self):
        conn = _make_db()
        result = parse_import_text(conn, "   ")
        assert result["status"] == "error"
        assert result["errors"]

    def test_happy_path_one_row_two_items(self):
        conn = _make_db()
        raw = "제품명\t위치\t잉크명\tCyan\tYellow\n" "P1\tA1\tBlue\t12.5\t3"
        result = parse_import_text(conn, raw)
        assert result["status"] == "ok"
        assert len(result["parsed_rows"]) == 1
        row = result["parsed_rows"][0]
        assert row["product_name"] == "P1"
        assert row["position"] == "A1"
        assert row["ink_name"] == "Blue"
        assert len(row["items"]) == 2
        weights = sorted(i["value_weight"] for i in row["items"])
        assert weights == [3.0, 12.5]

    def test_semifinished_format_does_not_require_ink_fields(self):
        conn = _make_db()
        raw = "반제품명\tCyan\tYellow\t비고\n" "BASE-100\t12.5\t3\t초도"
        result = parse_import_text(conn, raw)
        assert result["status"] == "ok"
        row = result["parsed_rows"][0]
        assert row["product_name"] == "BASE-100"
        assert row["position"] is None
        assert row["ink_name"] is None
        assert row["remark"] == "초도"
        weights = sorted(i["value_weight"] for i in row["items"])
        assert weights == [3.0, 12.5]

    def test_dash_cell_skipped(self):
        conn = _make_db()
        raw = "제품명\t위치\t잉크명\tCyan\tYellow\n" "P1\tA1\tBlue\t-\t3"
        result = parse_import_text(conn, raw)
        items = result["parsed_rows"][0]["items"]
        # '-' 셀은 항목 생성 안 함 → 1개만
        assert len(items) == 1
        assert items[0]["value_weight"] == 3.0

    def test_product_name_carry_over(self):
        conn = _make_db()
        # 둘째 데이터행은 제품명 생략 → 직전 제품명(P1) 승계
        raw = (
            "제품명\t위치\t잉크명\tCyan\n"
            "P1\tA1\tBlue\t10\n"
            "\tA2\tRed\t20"
        )
        result = parse_import_text(conn, raw)
        rows = result["parsed_rows"]
        assert len(rows) == 2
        assert rows[1]["product_name"] == "P1"  # 승계됨
        assert rows[1]["position"] == "A2"

    def test_remark_column_captured(self):
        conn = _make_db()
        raw = (
            "제품명\t위치\t잉크명\tCyan\t비고\n"
            "P1\tA1\tBlue\t10\t특이사항"
        )
        result = parse_import_text(conn, raw)
        row = result["parsed_rows"][0]
        assert row["remark"] == "특이사항"
        # 비고 컬럼은 자재 항목으로 잡히지 않음
        assert len(row["items"]) == 1

    def test_new_material_auto_registered_with_warning(self):
        conn = _make_db()
        raw = "제품명\t위치\t잉크명\tNewMat\n" "P1\tA1\tBlue\t5"
        result = parse_import_text(conn, raw)
        # 미등록 자재 → 자동 등록 + 경고
        assert any("자동 등록" in w["message"] for w in result["warnings"])
        new_id = conn.execute("SELECT id FROM materials WHERE name='NewMat'").fetchone()
        assert new_id is not None

    def test_missing_required_field_errors(self):
        conn = _make_db()
        raw = "반제품명\tCyan\n" "\t10"
        result = parse_import_text(conn, raw)
        assert any("반제품명" in e["message"] for e in result["errors"])
