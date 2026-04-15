"""Material stock tracking service.

Handles stock deductions from measurements, restocks, adjustments, and
discards. All operations log to material_stock_logs and update
materials.stock_quantity atomically within the caller's transaction.

Design: docs/02-design/features/material-stock-tracking.design.md
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..database import utc_now_text

VALID_REASONS = {"measurement", "restock", "adjust", "discard"}


def _current_stock(connection: sqlite3.Connection, material_id: int) -> float:
    row = connection.execute(
        "SELECT stock_quantity FROM materials WHERE id = ?",
        (material_id,),
    ).fetchone()
    return float(row["stock_quantity"]) if row else 0.0


def _insert_log(
    connection: sqlite3.Connection,
    *,
    material_id: int,
    delta: float,
    balance_after: float,
    reason: str,
    actor: dict[str, Any] | None,
    recipe_id: int | None = None,
    recipe_item_id: int | None = None,
    note: str | None = None,
) -> int:
    actor_id = actor.get("id") if actor else None
    actor_name = actor.get("display_name") or actor.get("username") if actor else None
    cursor = connection.execute(
        """
        INSERT INTO material_stock_logs
            (material_id, delta, balance_after, reason, actor_id, actor_name,
             recipe_id, recipe_item_id, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            delta,
            balance_after,
            reason,
            actor_id,
            actor_name,
            recipe_id,
            recipe_item_id,
            note,
            utc_now_text(),
        ),
    )
    return int(cursor.lastrowid)


def deduct_for_measurement(
    connection: sqlite3.Connection,
    *,
    material_id: int,
    weight: float,
    recipe_id: int,
    recipe_item_id: int,
    actor: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Deduct stock for a confirmed measurement. Idempotent per recipe_item_id.

    Returns the log entry dict, or None if skipped (zero weight / already logged).
    Caller owns the transaction.
    """
    if weight is None or weight <= 0:
        return None

    existing = connection.execute(
        "SELECT 1 FROM material_stock_logs WHERE recipe_item_id = ? AND reason = 'measurement'",
        (recipe_item_id,),
    ).fetchone()
    if existing:
        return None

    row = connection.execute(
        "UPDATE materials SET stock_quantity = stock_quantity - ? WHERE id = ? RETURNING stock_quantity",
        (weight, material_id),
    ).fetchone()
    if row is None:
        return None
    new_balance = float(row["stock_quantity"])
    note = "음수 재고 발생" if new_balance < 0 else None
    log_id = _insert_log(
        connection,
        material_id=material_id,
        delta=-weight,
        balance_after=new_balance,
        reason="measurement",
        actor=actor,
        recipe_id=recipe_id,
        recipe_item_id=recipe_item_id,
        note=note,
    )
    return {"log_id": log_id, "balance_after": new_balance, "negative": new_balance < 0}


def reverse_measurement(
    connection: sqlite3.Connection,
    *,
    recipe_item_id: int,
) -> None:
    """Undo a previous measurement deduction: credit stock back and remove log."""
    row = connection.execute(
        "SELECT id, material_id, delta FROM material_stock_logs WHERE recipe_item_id = ? AND reason = 'measurement'",
        (recipe_item_id,),
    ).fetchone()
    if not row:
        return
    connection.execute(
        "UPDATE materials SET stock_quantity = stock_quantity + ? WHERE id = ?",
        (-float(row["delta"]), int(row["material_id"])),
    )
    connection.execute("DELETE FROM material_stock_logs WHERE id = ?", (int(row["id"]),))


def _apply_delta(
    connection: sqlite3.Connection,
    *,
    material_id: int,
    delta: float,
    reason: str,
    actor: dict[str, Any] | None,
    note: str | None,
) -> dict[str, Any]:
    if reason not in VALID_REASONS:
        raise ValueError(f"invalid reason: {reason}")
    row = connection.execute(
        "UPDATE materials SET stock_quantity = stock_quantity + ? WHERE id = ? RETURNING stock_quantity",
        (delta, material_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"material not found: {material_id}")
    new_balance = float(row["stock_quantity"])
    current = new_balance - delta
    log_id = _insert_log(
        connection,
        material_id=material_id,
        delta=delta,
        balance_after=new_balance,
        reason=reason,
        actor=actor,
        note=note,
    )
    return {"log_id": log_id, "balance_before": current, "balance_after": new_balance, "delta": delta}


def restock(connection, *, material_id, amount, actor, note=None):
    if amount <= 0:
        raise ValueError("입고량은 0보다 커야 합니다.")
    return _apply_delta(connection, material_id=material_id, delta=amount, reason="restock", actor=actor, note=note)


def discard(connection, *, material_id, amount, actor, note):
    if amount <= 0:
        raise ValueError("폐기량은 0보다 커야 합니다.")
    if not note:
        raise ValueError("폐기 사유가 필요합니다.")
    return _apply_delta(connection, material_id=material_id, delta=-amount, reason="discard", actor=actor, note=note)


def adjust(connection, *, material_id, new_quantity, actor, note):
    if not note:
        raise ValueError("조정 사유가 필요합니다.")
    current = _current_stock(connection, material_id)
    delta = new_quantity - current
    return _apply_delta(connection, material_id=material_id, delta=delta, reason="adjust", actor=actor, note=note)


def set_threshold(connection: sqlite3.Connection, material_id: int, threshold: float) -> None:
    if threshold < 0:
        raise ValueError("임계치는 0 이상이어야 합니다.")
    connection.execute(
        "UPDATE materials SET stock_threshold = ? WHERE id = ?",
        (threshold, material_id),
    )


def stock_status(quantity: float, threshold: float) -> str:
    if quantity < 0:
        return "negative"
    if threshold > 0 and quantity <= threshold:
        return "low"
    return "ok"


def list_stock(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, name, category, stock_quantity, stock_threshold
        FROM materials
        WHERE is_active = 1
        ORDER BY name
        """
    ).fetchall()
    result = []
    for row in rows:
        qty = float(row["stock_quantity"] or 0)
        thr = float(row["stock_threshold"] or 0)
        result.append({
            "id": int(row["id"]),
            "name": row["name"],
            "category": row["category"],
            "stock_quantity": qty,
            "stock_threshold": thr,
            "status": stock_status(qty, thr),
        })
    return result


def list_logs(connection: sqlite3.Connection, material_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, delta, balance_after, reason, actor_name, recipe_id,
               recipe_item_id, note, created_at
        FROM material_stock_logs
        WHERE material_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (material_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
