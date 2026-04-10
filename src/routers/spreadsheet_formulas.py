"""Server-side formula engine for the spreadsheet editor.

Supported formula types:
  - SUM: sum of specified source columns
  - WEIGHTED: weighted sum with per-column coefficients
  - CUSTOM: restricted arithmetic expression using column references (c0, c1, ...)
"""

from __future__ import annotations

import ast
import json
import operator
from typing import Any

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_MAX_EXPRESSION_LENGTH = 200


def parse_formula_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def calculate_formula(
    formula_type: str | None,
    formula_params: dict[str, Any],
    row_values: dict[int, float],
) -> str | None:
    if not formula_type:
        return None

    if formula_type == "SUM":
        cols = formula_params.get("sourceColumns", [])
        return _fmt(sum(row_values.get(c, 0.0) for c in cols))

    if formula_type == "WEIGHTED":
        weights = formula_params.get("weights", {})
        total = sum(row_values.get(int(c), 0.0) * w for c, w in weights.items())
        return _fmt(total)

    if formula_type == "CUSTOM":
        expr = formula_params.get("expression", "")
        return _eval_safe(expr, row_values)

    return None


def calculate_row(
    columns: list[dict[str, Any]],
    cell_values: dict[int, str],
) -> dict[int, str]:
    """Calculate all formula columns for a single row.

    Args:
        columns: list of column dicts with keys: colIndex, colType, formulaType, formulaParams
        cell_values: mapping of colIndex -> raw string value

    Returns:
        mapping of colIndex -> calculated string value (only formula columns)
    """
    numeric_values: dict[int, float] = {}
    for col in columns:
        idx = col["colIndex"]
        if col["colType"] != "formula":
            raw = cell_values.get(idx, "")
            numeric_values[idx] = _to_float(raw)

    results: dict[int, str] = {}
    for col in columns:
        if col["colType"] != "formula":
            continue
        idx = col["colIndex"]
        val = calculate_formula(
            col.get("formulaType"),
            col.get("formulaParams", {}),
            numeric_values,
        )
        if val is not None:
            results[idx] = val
    return results


def _to_float(val: str | None) -> float:
    if not val:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _fmt(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _eval_safe(expr: str, row_values: dict[int, float]) -> str | None:
    if not expr or len(expr) > _MAX_EXPRESSION_LENGTH:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    try:
        result = _eval_node(tree.body, row_values)
        return _fmt(result)
    except Exception:
        return None


def _eval_node(node: ast.AST, vals: dict[int, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.Name):
        name = node.id
        if name.startswith("c") and name[1:].isdigit():
            return vals.get(int(name[1:]), 0.0)
        raise ValueError(f"Unknown variable: {name}")

    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = _eval_node(node.left, vals)
        right = _eval_node(node.right, vals)
        if isinstance(node.op, ast.Div) and right == 0:
            return 0.0
        return op_fn(left, right)

    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary: {type(node.op).__name__}")
        return op_fn(_eval_node(node.operand, vals))

    raise ValueError(f"Unsupported node: {type(node).__name__}")
