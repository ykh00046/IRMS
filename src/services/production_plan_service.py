"""Production plan service — plan CRUD + registered product management."""

from __future__ import annotations

from typing import Any

import sqlite3

from ..database import get_connection, utc_now_text, row_to_dict


def list_plans(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM production_plans ORDER BY created_at DESC"
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_plan(connection: sqlite3.Connection, plan_id: int) -> dict | None:
    row = connection.execute(
        "SELECT * FROM production_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def create_plan(connection: sqlite3.Connection, *, plan_name: str, week_start: str, week_end: str, created_by: str) -> dict:
    now = utc_now_text()
    cursor = connection.execute(
        "INSERT INTO production_plans (plan_name, week_start, week_end, status, created_by, created_at, updated_at) VALUES (?, ?, ?, 'draft', ?, ?, ?)",
        (plan_name, week_start, week_end, created_by, now, now),
    )
    connection.commit()
    return {"id": cursor.lastrowid, "plan_name": plan_name, "status": "draft", "created_at": now}


def save_schedules(connection: sqlite3.Connection, plan_id: int, schedules: list[dict]) -> int:
    """Save OCR-parsed schedule rows (with match results) to DB."""
    count = 0
    for s in schedules:
        connection.execute(
            """INSERT OR REPLACE INTO plan_schedules
               (plan_id, schedule_date, machine_no, line_type, shift, brand,
                ocr_product_name, matched_product_name, match_confidence, match_status, ink_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan_id, s.get("schedule_date"), s.get("machine_no"), s.get("line_type"),
                s.get("shift"), s.get("brand"), s.get("ocr_product_name"),
                s.get("matched_product_name"), s.get("match_confidence", 0),
                s.get("match_status", "pending"), s.get("ink_name"),
            ),
        )
        count += 1
    now = utc_now_text()
    connection.execute("UPDATE production_plans SET updated_at = ? WHERE id = ?", (now, plan_id))
    connection.commit()
    return count


def save_chemicals(connection: sqlite3.Connection, plan_id: int, chemicals: list[dict], schedule_date: str) -> int:
    count = 0
    for c in chemicals:
        connection.execute(
            """INSERT INTO plan_chemical_requests (plan_id, schedule_date, chemical_name, concentration, qty_3f, qty_1f)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (plan_id, schedule_date, c.get("chemical_name"), c.get("concentration"),
             c.get("qty_3f", 0), c.get("qty_1f", 0)),
        )
        count += 1
    connection.commit()
    return count


def get_schedules(connection: sqlite3.Connection, plan_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM plan_schedules WHERE plan_id = ? ORDER BY schedule_date, machine_no",
        (plan_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_chemicals(connection: sqlite3.Connection, plan_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT * FROM plan_chemical_requests WHERE plan_id = ? ORDER BY schedule_date, chemical_name",
        (plan_id,),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def confirm_match(connection: sqlite3.Connection, schedule_id: int, matched_name: str, ink_name: str | None = None) -> None:
    connection.execute(
        "UPDATE plan_schedules SET matched_product_name = ?, match_status = 'confirmed', match_confidence = 1.0, ink_name = ? WHERE id = ?",
        (matched_name, ink_name, schedule_id),
    )
    connection.commit()


def get_registered_products(connection: sqlite3.Connection) -> list[str]:
    """Get all unique product names from the business plan (사업계획 시트) data in ss_cells."""
    # Products are stored in ss_cells as product names linked through ss_products
    # Also gather from confirmed plan_schedules
    names: set[str] = set()

    # From existing confirmed schedules
    rows = connection.execute(
        "SELECT DISTINCT matched_product_name FROM plan_schedules WHERE match_status = 'confirmed' AND matched_product_name IS NOT NULL"
    ).fetchall()
    for r in rows:
        if r["matched_product_name"]:
            names.add(r["matched_product_name"])

    # From recipe product names
    rows = connection.execute(
        "SELECT DISTINCT product_name FROM recipes WHERE product_name IS NOT NULL"
    ).fetchall()
    for r in rows:
        if r["product_name"]:
            names.add(r["product_name"])

    return sorted(names)
