"""Spreadsheet editor API routes — product/column/row/cell CRUD + formula calculation."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_access_level
from ..database import get_connection, utc_now_text
from .spreadsheet_formulas import calculate_row, parse_formula_params


# ── Request models ──────────────────────────────────────────


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None


class ColumnCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    colType: str = Field("numeric")
    formulaType: str | None = None
    formulaParams: dict[str, Any] | None = None


class RowCells(BaseModel):
    rowIndex: int = Field(..., ge=0)
    cells: dict[str, str] = Field(default_factory=dict)


class SheetSave(BaseModel):
    rows: list[RowCells]


class CalcRequest(BaseModel):
    formulaType: str
    formulaParams: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, str] = Field(default_factory=dict)


# ── Helpers ─────────────────────────────────────────────────


def _col_dict(row) -> dict[str, Any]:
    params = parse_formula_params(row["formula_params"])
    d: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "colIndex": row["col_index"],
        "colType": row["col_type"],
        "isReadonly": bool(row["is_readonly"]),
    }
    if row["formula_type"]:
        d["formulaType"] = row["formula_type"]
        d["formulaParams"] = params
    return d


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

        computed = calculate_row(columns, cell_values)
        cell_values.update(computed)

        result.append({
            "id": db_row["id"],
            "rowIndex": db_row["row_index"],
            "cells": {str(k): v for k, v in cell_values.items()},
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
                "INSERT INTO ss_products (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (body.name, body.description, now, now),
            )
            product_id = cursor.lastrowid

            # Create default columns: 제품명, 위치, 잉크명
            defaults = [
                (product_id, "제품명", 0, "text", None, None, 1),
                (product_id, "위치", 1, "text", None, None, 0),
                (product_id, "잉크명", 2, "text", None, None, 0),
            ]
            conn.executemany(
                "INSERT INTO ss_columns (product_id, name, col_index, col_type, formula_type, formula_params, is_readonly) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                defaults,
            )
            conn.commit()
            return {"id": product_id, "name": body.name, "description": body.description, "createdAt": now}

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

                # Save non-formula cells
                for col_idx, val in cell_values.items():
                    col_db_id = col_index_to_id.get(col_idx)
                    if col_db_id is None:
                        continue
                    # Skip formula columns — they are calculated
                    col_info = next((c for c in columns if c["colIndex"] == col_idx), None)
                    if col_info and col_info["colType"] == "formula":
                        continue
                    conn.execute(
                        "INSERT INTO ss_cells (row_id, column_id, value) VALUES (?, ?, ?)",
                        (row_id, col_db_id, val),
                    )

                # Calculate formula columns
                computed = calculate_row(columns, cell_values)
                for col_idx, val in computed.items():
                    col_db_id = col_index_to_id.get(col_idx)
                    if col_db_id:
                        conn.execute(
                            "INSERT INTO ss_cells (row_id, column_id, value) VALUES (?, ?, ?)",
                            (row_id, col_db_id, val),
                        )

                all_cells = {str(k): v for k, v in cell_values.items()}
                all_cells.update({str(k): v for k, v in computed.items()})
                saved_rows.append({"rowIndex": row_data.rowIndex, "cells": all_cells})

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

            if body.colType not in ("text", "numeric", "formula"):
                raise HTTPException(status_code=400, detail="INVALID_COL_TYPE")

            col_index = _next_col_index(conn, product_id)
            is_readonly = 1 if body.colType == "formula" else 0
            params_json = json.dumps(body.formulaParams) if body.formulaParams else None

            cursor = conn.execute(
                "INSERT INTO ss_columns (product_id, name, col_index, col_type, formula_type, formula_params, is_readonly) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (product_id, body.name, col_index, body.colType, body.formulaType, params_json, is_readonly),
            )
            conn.execute("UPDATE ss_products SET updated_at = ? WHERE id = ?", (now, product_id))
            conn.commit()
            return {
                "id": cursor.lastrowid,
                "name": body.name,
                "colIndex": col_index,
                "colType": body.colType,
                "isReadonly": bool(is_readonly),
                "formulaType": body.formulaType,
                "formulaParams": body.formulaParams,
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

    # ── Formula calculation ─────────────────────────────

    @router.post("/calculate")
    async def calculate(body: CalcRequest) -> dict[str, Any]:
        from .spreadsheet_formulas import calculate_formula
        row_values = {int(k): float(v) for k, v in body.values.items() if v}
        result = calculate_formula(body.formulaType, body.formulaParams, row_values)
        return {"result": result}

    return router
