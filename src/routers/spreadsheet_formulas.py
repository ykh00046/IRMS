"""Excel-style formula engine for the spreadsheet editor.

Cells starting with '=' are treated as formulas.
Supported syntax:
  - Cell references: A1, B1, AA1 (row number ignored — same-row calculation)
  - Arithmetic: +, -, *, /, parentheses
  - Functions: SUM(B1:E1), ROUND(expr, digits)
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Any

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCTIONS = {"SUM", "ROUND"}

_MAX_EXPRESSION_LENGTH = 200

_CELL_REF_RE = re.compile(r"([A-Z]{1,2})(\d+)")

ERR = "#ERR"


# ── Public API ──────────────────────────────────────


def is_formula(value: str | None) -> bool:
    """Check if a cell value is a formula (starts with =)."""
    return bool(value and isinstance(value, str) and value.startswith("="))


def evaluate_cell(
    expression: str,
    row_values: dict[int, float],
) -> str:
    """Evaluate a single formula expression.

    Args:
        expression: formula string including leading '=' (e.g. "=B1+C1")
        row_values: {colIndex: numeric_value} for the current row

    Returns:
        Formatted result string, or '#ERR' on error.
    """
    expr = expression[1:].strip()  # strip leading '='
    if not expr or len(expression) > _MAX_EXPRESSION_LENGTH:
        return ERR

    # Rewrite cell references and function calls for AST parsing
    rewritten = _rewrite_expression(expr, row_values)
    if rewritten is None:
        return ERR

    try:
        tree = ast.parse(rewritten, mode="eval")
    except SyntaxError:
        return ERR

    try:
        result = _eval_node(tree.body, row_values)
        return _fmt(result)
    except Exception:
        return ERR


def evaluate_row(
    columns: list[dict[str, Any]],
    cell_values: dict[int, str],
) -> dict[int, str]:
    """Evaluate all formula cells in a single row.

    Args:
        columns: list of column dicts with 'colIndex' key
        cell_values: {colIndex: raw_string_value}

    Returns:
        {colIndex: calculated_string} for formula cells only.
    """
    # Build numeric lookup from non-formula cells
    numeric_values: dict[int, float] = {}
    for col in columns:
        idx = col["colIndex"]
        raw = cell_values.get(idx, "")
        if not is_formula(raw):
            numeric_values[idx] = _to_float(raw)

    results: dict[int, str] = {}
    for col in columns:
        idx = col["colIndex"]
        raw = cell_values.get(idx, "")
        if is_formula(raw):
            results[idx] = evaluate_cell(raw, numeric_values)

    return results


# ── Cell reference helpers ──────────────────────────


def _col_letter_to_index(letters: str) -> int:
    """Convert column letters to 0-based index. A=0, B=1, ..., Z=25, AA=26."""
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def _col_index_to_letter(index: int) -> str:
    """Convert 0-based column index to letters. 0=A, 1=B, ..., 25=Z, 26=AA."""
    result = ""
    idx = index + 1
    while idx > 0:
        idx, remainder = divmod(idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _parse_cell_ref(ref: str) -> int:
    """Parse 'B1' -> colIndex 1. Row number is ignored (same-row calculation)."""
    m = _CELL_REF_RE.fullmatch(ref.upper())
    if not m:
        raise ValueError(f"Invalid cell reference: {ref}")
    return _col_letter_to_index(m.group(1))


def _expand_range(start_ref: str, end_ref: str) -> list[int]:
    """Expand 'B1:E1' -> [1, 2, 3, 4] (column indices)."""
    start_col = _parse_cell_ref(start_ref)
    end_col = _parse_cell_ref(end_ref)
    if start_col > end_col:
        start_col, end_col = end_col, start_col
    return list(range(start_col, end_col + 1))


# ── Expression rewriting ───────────────────────────


def _rewrite_expression(expr: str, row_values: dict[int, float]) -> str | None:
    """Rewrite cell references in the expression to variable names for AST.

    A1 -> __c0, B1 -> __c1, etc.
    SUM(B1:E1) -> __SUM_B1_E1  (pre-computed as a constant)
    ROUND(expr, n) -> kept as function call with __ROUND
    """
    try:
        result = _rewrite_functions(expr, row_values)
        result = _CELL_REF_RE.sub(lambda m: f"__c{_col_letter_to_index(m.group(1))}", result)
        return result
    except Exception:
        return None


def _rewrite_functions(expr: str, row_values: dict[int, float]) -> str:
    """Pre-process SUM(range) calls by replacing them with computed values."""
    # Handle SUM(X1:Y1) — replace with pre-computed float
    sum_pattern = re.compile(r"SUM\(([A-Z]{1,2}\d+):([A-Z]{1,2}\d+)\)", re.IGNORECASE)
    result = expr
    for m in sum_pattern.finditer(expr):
        cols = _expand_range(m.group(1), m.group(2))
        total = sum(row_values.get(c, 0.0) for c in cols)
        result = result.replace(m.group(0), str(total))

    return result


# ── AST evaluation ─────────────────────────────────


def _eval_node(node: ast.AST, vals: dict[int, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)

    if isinstance(node, ast.Name):
        name = node.id
        # __cN -> column reference
        if name.startswith("__c") and name[3:].isdigit():
            return vals.get(int(name[3:]), 0.0)
        raise ValueError(f"Unknown variable: {name}")

    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = _eval_node(node.left, vals)
        right = _eval_node(node.right, vals)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ZeroDivisionError("Division by zero")
        return op_fn(left, right)

    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary: {type(node.op).__name__}")
        return op_fn(_eval_node(node.operand, vals))

    # Function calls: ROUND(expr, n)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Unsupported call")
        func_name = node.func.id.upper()
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"Unknown function: {func_name}")

        if func_name == "ROUND":
            if len(node.args) != 2:
                raise ValueError("ROUND requires 2 arguments")
            value = _eval_node(node.args[0], vals)
            digits = int(_eval_node(node.args[1], vals))
            return round(value, digits)

        if func_name == "SUM":
            # SUM with individual args (non-range, already expanded)
            return sum(_eval_node(arg, vals) for arg in node.args)

        raise ValueError(f"Unhandled function: {func_name}")

    raise ValueError(f"Unsupported node: {type(node).__name__}")


# ── Formatting helpers ─────────────────────────────


def _to_float(val: str | None) -> float:
    if not val:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _fmt(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
