"""Parse mixed-content recipe cell values from Excel imports.

Splits raw cells like "12.50 (HR10)" or "APB(17) 360" into a numeric weight
and a residual text memo, following the "last number wins" rule defined in
docs/02-design/features/excel-recipe-migration.design.md §5.
"""

from __future__ import annotations

import re

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_SPLIT_RE = re.compile(r"[\s:,]+")
_FORMULA_PREFIX = "="


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def parse_cell(raw: object) -> tuple[float | None, str | None]:
    """Return (value_weight, value_text) from a raw cell value.

    Rules:
      - None / empty              -> (None, None)
      - "-" placeholder           -> (None, "-")
      - Pure numeric              -> (float, None)
      - Formula (starts with '=') -> (None, <raw string>)
      - Mixed: parenthesised content is preserved as memo; the last whitespace-
        separated token outside parens that is a pure number becomes the weight.
        Hyphenated codes like "BYK-199" are NOT split — only tokens that parse
        as float() are treated as numeric.
    """
    if raw is None:
        return None, None

    text = str(raw).strip()
    if not text:
        return None, None

    if text == "-":
        return None, "-"

    if text.startswith(_FORMULA_PREFIX):
        return None, text

    try:
        return float(text), None
    except ValueError:
        pass

    paren_contents = _PAREN_RE.findall(text)
    outside = _PAREN_RE.sub(" ", text)

    tokens = [t for t in _SPLIT_RE.split(outside) if t]
    numeric_tokens = [float(t) for t in tokens if _is_number(t)]
    text_tokens = [t for t in tokens if not _is_number(t)]

    if not numeric_tokens:
        return None, text

    value = numeric_tokens[-1]

    parts: list[str] = []
    if text_tokens:
        parts.append(" ".join(text_tokens))
    for memo in paren_contents:
        memo = memo.strip()
        if memo:
            parts.append(f"({memo})")
    text_out = " ".join(parts) if parts else None
    return value, text_out
