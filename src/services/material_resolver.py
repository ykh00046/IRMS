"""Material name normalization and lookup.

Resolves raw material names from Excel imports or user input to `materials.id`,
tolerating case, whitespace, and alias variations.
"""

from __future__ import annotations

import sqlite3


def normalize_material_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(str(name).strip().upper().split())


def resolve_material(
    connection: sqlite3.Connection,
    raw_name: str | None,
) -> int | None:
    """Return material_id by normalized name or alias. None if not found."""
    normalized = normalize_material_name(raw_name)
    if not normalized:
        return None

    row = connection.execute(
        """
        SELECT id
        FROM materials
        WHERE is_active = 1
          AND REPLACE(UPPER(TRIM(name)), '  ', ' ') = ?
        """,
        (normalized,),
    ).fetchone()
    if row:
        return int(row["id"])

    row = connection.execute(
        """
        SELECT material_id
        FROM material_aliases
        WHERE REPLACE(UPPER(TRIM(alias_name)), '  ', ' ') = ?
        """,
        (normalized,),
    ).fetchone()
    return int(row["material_id"]) if row else None


def resolve_materials_bulk(
    connection: sqlite3.Connection,
    raw_names: list[str],
) -> dict[str, int | None]:
    """Resolve a batch of names in one pass. Returns {raw_name: material_id | None}."""
    return {name: resolve_material(connection, name) for name in raw_names}
