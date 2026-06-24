import sqlite3

from src.services import variance_service


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT
        );
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            ink_name TEXT NOT NULL
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            value_weight REAL,
            actual_weight REAL,
            measured_at TEXT,
            measured_by TEXT
        );
        """
    )
    return conn


def _seed(conn: sqlite3.Connection) -> tuple[int, int]:
    mat_a = conn.execute(
        "INSERT INTO materials (name, category) VALUES ('A', 'ink')"
    ).lastrowid
    mat_b = conn.execute(
        "INSERT INTO materials (name, category) VALUES ('B', 'ink')"
    ).lastrowid
    recipe = conn.execute(
        "INSERT INTO recipes (product_name, ink_name) VALUES ('P1', 'I1')"
    ).lastrowid
    rows = [
        (recipe, mat_a, 100.0, 110.0, "2026-06-18T01:00:00Z", "op1"),
        (recipe, mat_a, 50.0, 45.0, "2026-06-18T02:00:00Z", "op1"),
        (recipe, mat_b, 20.0, None, "2026-06-18T03:00:00Z", "op2"),
    ]
    conn.executemany(
        """
        INSERT INTO recipe_items
            (recipe_id, material_id, value_weight, actual_weight, measured_at, measured_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return int(mat_a), int(mat_b)


def test_variance_summary_uses_actual_when_available_and_target_as_fallback():
    conn = _make_db()
    _seed(conn)

    summary = variance_service.variance_summary(
        conn, "2026-06-18T00:00:00Z", "2026-06-18T23:59:59Z"
    )

    assert summary["measured_count"] == 3
    assert summary["actual_count"] == 2
    assert summary["coverage_pct"] == 66.67
    assert summary["target_total_g"] == 170.0
    assert summary["actual_total_g"] == 175.0
    assert summary["deviation_total_g"] == 5.0
    assert summary["absolute_deviation_total_g"] == 15.0


def test_top_material_variances_excludes_materials_without_actual_weight():
    conn = _make_db()
    mat_a, mat_b = _seed(conn)

    items = variance_service.top_material_variances(
        conn, "2026-06-18T00:00:00Z", "2026-06-18T23:59:59Z"
    )

    assert [item["material_id"] for item in items] == [mat_a]
    assert items[0]["deviation_g"] == 5.0
    assert items[0]["absolute_deviation_g"] == 15.0
    assert mat_b not in [item["material_id"] for item in items]


def test_material_variance_recipes_orders_by_absolute_deviation():
    conn = _make_db()
    mat_a, _ = _seed(conn)

    recipes = variance_service.material_variance_recipes(
        conn, mat_a, "2026-06-18T00:00:00Z", "2026-06-18T23:59:59Z"
    )

    assert [row["deviation_g"] for row in recipes] == [10.0, -5.0]
    assert recipes[0]["deviation_pct"] == 10.0
