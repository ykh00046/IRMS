from __future__ import annotations

import sqlite3


def test_fresh_schema_does_not_create_inventory_tables(monkeypatch, tmp_path) -> None:
    import src.db.connection as connection_module
    from src.db.schema import init_db

    data_dir = tmp_path / "data"
    monkeypatch.setattr(connection_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(connection_module, "DATABASE_PATH", data_dir / "irms.db")

    init_db()

    with sqlite3.connect(data_dir / "irms.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        material_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(materials)").fetchall()
        }

    assert "material_stock_logs" not in tables
    assert "material_lots" not in tables
    assert "purchase_orders" not in tables
    assert "purchase_order_items" not in tables
    assert "po_receipts" not in tables
    assert "po_receipt_items" not in tables
    assert "stock_quantity" not in material_columns
    assert "stock_threshold" not in material_columns
    assert "lead_time_days" not in material_columns
    assert "reorder_cycle_days" not in material_columns
    assert "ss_products" not in tables
    assert "ss_columns" not in tables
    assert "ss_rows" not in tables
    assert "ss_cells" not in tables
