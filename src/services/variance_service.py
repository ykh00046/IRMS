from __future__ import annotations

from typing import Any

import sqlite3


def _deviation_fields(target: float | None, actual: float | None) -> dict[str, Any]:
    if target is None or actual is None:
        return {
            "target_weight_g": round(float(target or 0.0), 2),
            "actual_weight_g": None if actual is None else round(float(actual), 2),
            "deviation_g": 0.0,
            "deviation_pct": None,
        }
    deviation = float(actual) - float(target)
    pct = (deviation / float(target) * 100.0) if float(target) != 0 else None
    return {
        "target_weight_g": round(float(target), 2),
        "actual_weight_g": round(float(actual), 2),
        "deviation_g": round(deviation, 2),
        "deviation_pct": None if pct is None else round(pct, 2),
    }


def variance_summary(connection: sqlite3.Connection, from_ts: str, to_ts: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS measured_count,
               SUM(CASE WHEN actual_weight IS NOT NULL THEN 1 ELSE 0 END) AS actual_count,
               COALESCE(SUM(value_weight), 0) AS target_total,
               COALESCE(SUM(COALESCE(actual_weight, value_weight)), 0) AS actual_total,
               COALESCE(SUM(ABS(COALESCE(actual_weight, value_weight) - value_weight)), 0) AS abs_total
        FROM recipe_items
        WHERE measured_at IS NOT NULL
          AND measured_at BETWEEN ? AND ?
        """,
        (from_ts, to_ts),
    ).fetchone()
    measured_count = int(row["measured_count"] or 0)
    actual_count = int(row["actual_count"] or 0)
    target_total = float(row["target_total"] or 0.0)
    actual_total = float(row["actual_total"] or 0.0)
    deviation_total = actual_total - target_total
    deviation_pct = (deviation_total / target_total * 100.0) if target_total else None
    return {
        "measured_count": measured_count,
        "actual_count": actual_count,
        "coverage_pct": round((actual_count / measured_count * 100.0), 2) if measured_count else 0.0,
        "target_total_g": round(target_total, 2),
        "actual_total_g": round(actual_total, 2),
        "deviation_total_g": round(deviation_total, 2),
        "deviation_pct": None if deviation_pct is None else round(deviation_pct, 2),
        "absolute_deviation_total_g": round(float(row["abs_total"] or 0.0), 2),
    }


def top_material_variances(
    connection: sqlite3.Connection,
    from_ts: str,
    to_ts: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT m.id AS material_id,
               m.name AS material_name,
               m.category AS category,
               COUNT(*) AS measured_count,
               SUM(CASE WHEN ri.actual_weight IS NOT NULL THEN 1 ELSE 0 END) AS actual_count,
               COALESCE(SUM(ri.value_weight), 0) AS target_total,
               COALESCE(SUM(COALESCE(ri.actual_weight, ri.value_weight)), 0) AS actual_total,
               COALESCE(SUM(ABS(COALESCE(ri.actual_weight, ri.value_weight) - ri.value_weight)), 0) AS abs_total
        FROM recipe_items ri
        JOIN materials m ON m.id = ri.material_id
        WHERE ri.measured_at IS NOT NULL
          AND ri.measured_at BETWEEN ? AND ?
        GROUP BY m.id, m.name, m.category
        HAVING actual_count > 0
        ORDER BY abs_total DESC, material_name ASC
        LIMIT ?
        """,
        (from_ts, to_ts, limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        target_total = float(row["target_total"] or 0.0)
        actual_total = float(row["actual_total"] or 0.0)
        deviation = actual_total - target_total
        items.append(
            {
                "material_id": int(row["material_id"]),
                "material_name": row["material_name"],
                "category": row["category"] or "",
                "measured_count": int(row["measured_count"] or 0),
                "actual_count": int(row["actual_count"] or 0),
                "target_total_g": round(target_total, 2),
                "actual_total_g": round(actual_total, 2),
                "deviation_g": round(deviation, 2),
                "deviation_pct": round((deviation / target_total * 100.0), 2) if target_total else None,
                "absolute_deviation_g": round(float(row["abs_total"] or 0.0), 2),
            }
        )
    return items


def material_variance_recipes(
    connection: sqlite3.Connection,
    material_id: int,
    from_ts: str,
    to_ts: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT r.id AS recipe_id,
               r.product_name,
               r.ink_name,
               ri.value_weight,
               ri.actual_weight,
               ri.measured_at,
               ri.measured_by
        FROM recipe_items ri
        JOIN recipes r ON r.id = ri.recipe_id
        WHERE ri.material_id = ?
          AND ri.measured_at IS NOT NULL
          AND ri.actual_weight IS NOT NULL
          AND ri.measured_at BETWEEN ? AND ?
        ORDER BY ABS(ri.actual_weight - ri.value_weight) DESC, ri.measured_at DESC
        LIMIT ?
        """,
        (material_id, from_ts, to_ts, limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = {
            "recipe_id": int(row["recipe_id"]),
            "product_name": row["product_name"],
            "ink_name": row["ink_name"],
            "measured_at": row["measured_at"],
            "measured_by": row["measured_by"] or "(unknown)",
        }
        payload.update(_deviation_fields(row["value_weight"], row["actual_weight"]))
        items.append(payload)
    return items
