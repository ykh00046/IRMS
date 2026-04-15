"""Spreadsheet editor API routes — product/column/row/cell CRUD + excel-style formulas."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_access_level
from ..database import get_connection, utc_now_text
from .spreadsheet_formulas import evaluate_row, is_formula


# ── Request models ──────────────────────────────────────────


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    recipeType: str = Field("solution", pattern="^(solution|powder)$")


# Default columns per recipe type, based on reference excel files.
# Numeric material columns are the ones operators weigh; TOTAL/BINDER are excel formulas.
_DEFAULT_COLUMNS: dict[str, list[tuple[str, str, str | None]]] = {
    "solution": [
        ("제품명", "text", None),
        ("위치", "text", None),
        ("잉크명", "text", None),
        ("PL-835-1", "numeric", None),
        ("PL-150-2", "numeric", None),
        ("PL-135", "numeric", None),
        ("PL-580-2", "numeric", None),
        ("PL-345-3", "numeric", None),
        ("PL-700-2", "numeric", None),
        ("PL-735", "numeric", None),
        ("TOTAL", "numeric", "=SUM(D1:J1)"),
        ("BINDER(PCB)", "numeric", "=(D1*0.75)+(E1*0.8)+(F1*0.75)+(G1*0.75)+(H1*0.77)+(I1*0.57)+(J1*0.5)"),
    ],
    "powder": [
        ("제품명", "text", None),
        ("위치", "text", None),
        ("잉크명", "text", None),
        ("RAVEN", "numeric", None),
        ("BLACK", "numeric", None),
        ("RED", "numeric", None),
        ("BLUE", "numeric", None),
        ("YELLOW", "numeric", None),
        ("WHITE", "numeric", None),
        ("TTO-55(b)", "numeric", None),
        ("PB", "text", None),
        ("HDI", "text", None),
    ],
}


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None


class ColumnCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    colType: str = Field("numeric")


class RowCells(BaseModel):
    rowIndex: int = Field(..., ge=0)
    cells: dict[str, str] = Field(default_factory=dict)


class SheetSave(BaseModel):
    rows: list[RowCells]


# ── Helpers ─────────────────────────────────────────────────


def _col_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "colIndex": row["col_index"],
        "colType": row["col_type"],
        "isReadonly": bool(row["is_readonly"]),
    }


def _load_columns(conn, product_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM ss_columns WHERE product_id = ? ORDER BY col_index",
        (product_id,),
    ).fetchall()
    return [_col_dict(r) for r in rows]


def _load_sheet_data(conn, product_id: int, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    db_rows = conn.execute(
        "SELECT * FROM ss_rows WHERE product_id = ? ORDER BY row_index",
        (product_id,),
    ).fetchall()

    col_id_to_index: dict[int, int] = {}
    for c in columns:
        col_id_to_index[c["id"]] = c["colIndex"]

    result = []
    for db_row in db_rows:
        cells_raw = conn.execute(
            "SELECT column_id, value FROM ss_cells WHERE row_id = ?",
            (db_row["id"],),
        ).fetchall()

        cell_values: dict[int, str] = {}
        for cell in cells_raw:
            idx = col_id_to_index.get(cell["column_id"])
            if idx is not None:
                cell_values[idx] = cell["value"] or ""

        # Evaluate formula cells
        computed = evaluate_row(columns, cell_values)

        # Build response: formula cells get {formula, display}, others get plain string
        cells_out: dict[str, Any] = {}
        for idx, val in cell_values.items():
            key = str(idx)
            if is_formula(val):
                cells_out[key] = {"formula": val, "display": computed.get(idx, "#ERR")}
            else:
                cells_out[key] = val

        result.append({
            "id": db_row["id"],
            "rowIndex": db_row["row_index"],
            "cells": cells_out,
        })
    return result


def _next_col_index(conn, product_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(col_index) AS mx FROM ss_columns WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    return (row["mx"] or -1) + 1


def _require_product(conn, product_id: int):
    row = conn.execute("SELECT * FROM ss_products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="PRODUCT_NOT_FOUND")
    return row


# ── Router builder ──────────────────────────────────────────


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    # ── Products ────────────────────────────────────────

    @router.get("/products")
    async def list_products() -> dict[str, Any]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ss_products ORDER BY name"
            ).fetchall()
            items = []
            for r in rows:
                col_count = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM ss_columns WHERE product_id = ?", (r["id"],)
                ).fetchone()["cnt"]
                row_count = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM ss_rows WHERE product_id = ?", (r["id"],)
                ).fetchone()["cnt"]
                items.append({
                    "id": r["id"],
                    "name": r["name"],
                    "description": r["description"],
                    "recipeType": r["recipe_type"] if "recipe_type" in r.keys() else "solution",
                    "columnCount": col_count,
                    "rowCount": row_count,
                    "updatedAt": r["updated_at"],
                })
            return {"items": items}

    @router.post("/products", status_code=201)
    async def create_product(body: ProductCreate) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM ss_products WHERE name = ?", (body.name,)
            ).fetchone()
            if existing:
                raise HTTPException(status_code=409, detail="PRODUCT_NAME_EXISTS")

            cursor = conn.execute(
                "INSERT INTO ss_products (name, description, recipe_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (body.name, body.description, body.recipeType, now, now),
            )
            product_id = cursor.lastrowid

            template = _DEFAULT_COLUMNS.get(body.recipeType, _DEFAULT_COLUMNS["solution"])
            defaults = []
            for idx, (col_name, col_type, formula) in enumerate(template):
                is_readonly = 1 if idx == 0 else 0
                defaults.append((product_id, col_name, idx, col_type, None, None, is_readonly))
            conn.executemany(
                "INSERT INTO ss_columns (product_id, name, col_index, col_type, formula_type, formula_params, is_readonly) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                defaults,
            )

            # Seed first row with default formulas (TOTAL / BINDER) so operators see them pre-filled.
            has_formulas = any(f for _, _, f in template)
            if has_formulas:
                row_cursor = conn.execute(
                    "INSERT INTO ss_rows (product_id, row_index) VALUES (?, 0)",
                    (product_id,),
                )
                row_id = row_cursor.lastrowid
                col_rows = conn.execute(
                    "SELECT id, col_index FROM ss_columns WHERE product_id = ? ORDER BY col_index",
                    (product_id,),
                ).fetchall()
                for col_row, (_, _, formula) in zip(col_rows, template):
                    if formula:
                        conn.execute(
                            "INSERT INTO ss_cells (row_id, column_id, value) VALUES (?, ?, ?)",
                            (row_id, col_row["id"], formula),
                        )

            conn.commit()
            return {
                "id": product_id,
                "name": body.name,
                "description": body.description,
                "recipeType": body.recipeType,
                "createdAt": now,
            }

    @router.patch("/products/{product_id}")
    async def update_product(product_id: int, body: ProductUpdate) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            product = _require_product(conn, product_id)
            new_name = body.name or product["name"]
            new_desc = body.description if body.description is not None else product["description"]

            if body.name and body.name != product["name"]:
                dup = conn.execute(
                    "SELECT 1 FROM ss_products WHERE name = ? AND id != ?", (body.name, product_id)
                ).fetchone()
                if dup:
                    raise HTTPException(status_code=409, detail="PRODUCT_NAME_EXISTS")

            conn.execute(
                "UPDATE ss_products SET name = ?, description = ?, updated_at = ? WHERE id = ?",
                (new_name, new_desc, now, product_id),
            )
            conn.commit()
            return {"id": product_id, "name": new_name, "description": new_desc, "updatedAt": now}

    @router.delete("/products/{product_id}")
    async def delete_product(product_id: int) -> dict[str, Any]:
        with get_connection() as conn:
            _require_product(conn, product_id)
            conn.execute("DELETE FROM ss_products WHERE id = ?", (product_id,))
            conn.commit()
            return {"deleted": True}

    # ── Sheet load/save ─────────────────────────────────

    @router.get("/products/{product_id}/sheet")
    async def load_sheet(product_id: int) -> dict[str, Any]:
        with get_connection() as conn:
            product = _require_product(conn, product_id)
            columns = _load_columns(conn, product_id)
            rows = _load_sheet_data(conn, product_id, columns)
            return {
                "product": {"id": product["id"], "name": product["name"], "description": product["description"]},
                "columns": columns,
                "rows": rows,
            }

    @router.post("/products/{product_id}/save")
    async def save_sheet(product_id: int, body: SheetSave) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            _require_product(conn, product_id)
            columns = _load_columns(conn, product_id)

            col_index_to_id: dict[int, int] = {c["colIndex"]: c["id"] for c in columns}

            # Delete existing rows (full overwrite)
            conn.execute("DELETE FROM ss_rows WHERE product_id = ?", (product_id,))

            saved_rows = []
            for row_data in body.rows:
                cursor = conn.execute(
                    "INSERT INTO ss_rows (product_id, row_index) VALUES (?, ?)",
                    (product_id, row_data.rowIndex),
                )
                row_id = cursor.lastrowid

                cell_values: dict[int, str] = {}
                for col_idx_str, val in row_data.cells.items():
                    col_idx = int(col_idx_str)
                    cell_values[col_idx] = val

                # Save all cells as-is (including formula text like "=A1+B1")
                for col_idx, val in cell_values.items():
                    col_db_id = col_index_to_id.get(col_idx)
                    if col_db_id is None:
                        continue
                    conn.execute(
                        "INSERT INTO ss_cells (row_id, column_id, value) VALUES (?, ?, ?)",
                        (row_id, col_db_id, val),
                    )

                # Evaluate formulas for response
                computed = evaluate_row(columns, cell_values)

                cells_out: dict[str, Any] = {}
                for idx, val in cell_values.items():
                    key = str(idx)
                    if is_formula(val):
                        cells_out[key] = {"formula": val, "display": computed.get(idx, "#ERR")}
                    else:
                        cells_out[key] = val
                saved_rows.append({"rowIndex": row_data.rowIndex, "cells": cells_out})

            conn.execute(
                "UPDATE ss_products SET updated_at = ? WHERE id = ?", (now, product_id)
            )
            conn.commit()
            return {"saved": True, "rowCount": len(saved_rows), "rows": saved_rows}

    # ── Columns ─────────────────────────────────────────

    @router.post("/products/{product_id}/columns", status_code=201)
    async def add_column(product_id: int, body: ColumnCreate) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            _require_product(conn, product_id)

            col_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM ss_columns WHERE product_id = ?", (product_id,)
            ).fetchone()["cnt"]
            if col_count >= 30:
                raise HTTPException(status_code=400, detail="COLUMN_LIMIT_EXCEEDED")

            if body.colType not in ("text", "numeric"):
                raise HTTPException(status_code=400, detail="INVALID_COL_TYPE")

            col_index = _next_col_index(conn, product_id)

            cursor = conn.execute(
                "INSERT INTO ss_columns (product_id, name, col_index, col_type, is_readonly) "
                "VALUES (?, ?, ?, ?, 0)",
                (product_id, body.name, col_index, body.colType),
            )
            conn.execute("UPDATE ss_products SET updated_at = ? WHERE id = ?", (now, product_id))
            conn.commit()
            return {
                "id": cursor.lastrowid,
                "name": body.name,
                "colIndex": col_index,
                "colType": body.colType,
                "isReadonly": False,
            }

    @router.delete("/columns/{column_id}")
    async def delete_column(column_id: int) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            col = conn.execute("SELECT * FROM ss_columns WHERE id = ?", (column_id,)).fetchone()
            if not col:
                raise HTTPException(status_code=404, detail="COLUMN_NOT_FOUND")
            product_id = col["product_id"]
            conn.execute("DELETE FROM ss_columns WHERE id = ?", (column_id,))
            conn.execute("UPDATE ss_products SET updated_at = ? WHERE id = ?", (now, product_id))
            conn.commit()
            return {"deleted": True}

    # ── Rows ────────────────────────────────────────────

    @router.post("/products/{product_id}/rows", status_code=201)
    async def add_row(product_id: int) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            _require_product(conn, product_id)
            next_idx_row = conn.execute(
                "SELECT MAX(row_index) AS mx FROM ss_rows WHERE product_id = ?", (product_id,)
            ).fetchone()
            next_idx = (next_idx_row["mx"] or -1) + 1

            cursor = conn.execute(
                "INSERT INTO ss_rows (product_id, row_index) VALUES (?, ?)",
                (product_id, next_idx),
            )
            conn.execute("UPDATE ss_products SET updated_at = ? WHERE id = ?", (now, product_id))
            conn.commit()
            return {"id": cursor.lastrowid, "rowIndex": next_idx}

    @router.delete("/rows/{row_id}")
    async def delete_row(row_id: int) -> dict[str, Any]:
        now = utc_now_text()
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM ss_rows WHERE id = ?", (row_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="ROW_NOT_FOUND")
            product_id = row["product_id"]
            conn.execute("DELETE FROM ss_rows WHERE id = ?", (row_id,))
            conn.execute("UPDATE ss_products SET updated_at = ? WHERE id = ?", (now, product_id))
            conn.commit()
            return {"deleted": True}

    return router
