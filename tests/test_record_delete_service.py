import sqlite3

from src.services import record_delete_service as deletes


def _make_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT NOT NULL,
            revision_of INTEGER
        );
        CREATE TABLE recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE
        );
        CREATE TABLE blend_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_lot TEXT NOT NULL,
            recipe_id INTEGER REFERENCES recipes(id),
            product_name TEXT NOT NULL
        );
        CREATE TABLE blend_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER NOT NULL REFERENCES blend_records(id) ON DELETE CASCADE
        );
        CREATE TABLE viscosity_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blend_record_id INTEGER
        );
        """
    )
    return connection


def _seed_recipe_with_record(connection: sqlite3.Connection) -> tuple[int, int]:
    recipe_id = int(
        connection.execute(
            "INSERT INTO recipes (product_name) VALUES ('BASE-100')"
        ).lastrowid
    )
    connection.execute("INSERT INTO recipe_items (recipe_id) VALUES (?)", (recipe_id,))
    record_id = int(
        connection.execute(
            """
            INSERT INTO blend_records (product_lot, recipe_id, product_name)
            VALUES ('BASE26063001', ?, 'BASE-100')
            """,
            (recipe_id,),
        ).lastrowid
    )
    connection.execute("INSERT INTO blend_details (blend_record_id) VALUES (?)", (record_id,))
    connection.execute("INSERT INTO viscosity_readings (blend_record_id) VALUES (?)", (record_id,))
    return recipe_id, record_id


def test_delete_recipe_preserves_blend_records_when_requested() -> None:
    connection = _make_db()
    recipe_id, record_id = _seed_recipe_with_record(connection)

    result = deletes.delete_recipe(connection, recipe_id, delete_linked_records=False)

    assert result is not None
    assert result.deleted_linked_records is False
    assert result.linked_record_count == 1
    assert connection.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone() is None
    record = connection.execute(
        "SELECT recipe_id FROM blend_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    assert record is not None
    assert record["recipe_id"] is None
    assert connection.execute(
        "SELECT 1 FROM blend_details WHERE blend_record_id = ?",
        (record_id,),
    ).fetchone() is not None


def test_delete_recipe_removes_linked_blend_records_when_requested() -> None:
    connection = _make_db()
    recipe_id, record_id = _seed_recipe_with_record(connection)

    result = deletes.delete_recipe(connection, recipe_id, delete_linked_records=True)

    assert result is not None
    assert result.deleted_linked_records is True
    assert result.linked_record_count == 1
    assert connection.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone() is None
    assert connection.execute("SELECT 1 FROM blend_records WHERE id = ?", (record_id,)).fetchone() is None
    assert connection.execute(
        "SELECT 1 FROM blend_details WHERE blend_record_id = ?",
        (record_id,),
    ).fetchone() is None
    reading = connection.execute("SELECT blend_record_id FROM viscosity_readings").fetchone()
    assert reading["blend_record_id"] is None


def test_delete_blend_record_removes_detail_and_unlinks_viscosity() -> None:
    connection = _make_db()
    _, record_id = _seed_recipe_with_record(connection)

    result = deletes.delete_blend_record(connection, record_id)

    assert result is not None
    assert result.product_lot == "BASE26063001"
    assert connection.execute("SELECT 1 FROM blend_records WHERE id = ?", (record_id,)).fetchone() is None
    assert connection.execute(
        "SELECT 1 FROM blend_details WHERE blend_record_id = ?",
        (record_id,),
    ).fetchone() is None
    reading = connection.execute("SELECT blend_record_id FROM viscosity_readings").fetchone()
    assert reading["blend_record_id"] is None
