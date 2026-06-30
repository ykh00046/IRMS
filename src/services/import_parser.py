import sqlite3
from typing import Any

from ..db import normalize_token
from .cell_value_parser import parse_cell


def _auto_register_material(connection: sqlite3.Connection, name: str) -> dict[str, Any]:
    """Register an unknown material and return its payload."""
    cursor = connection.execute(
        """
        INSERT INTO materials (name, unit_type, unit, color_group, category, is_active)
        VALUES (?, 'weight', 'g', 'none', '미분류', 1)
        """,
        (name,),
    )
    return {
        "id": cursor.lastrowid,
        "name": name,
        "unit_type": "weight",
        "unit": "g",
        "color_group": "none",
        "category": "미분류",
    }


def _parse_value(raw: str) -> tuple[float | None, str | None]:
    """Delegate to the canonical `parse_cell` parser.

    Tab-paste importer treats "-" as a skip (returns (None, None)) so that
    placeholder rows don't create empty text items.
    """
    stripped = raw.strip().replace(",", "") if raw else ""
    if stripped == "-":
        return None, None
    return parse_cell(stripped)


def parse_import_text(connection: sqlite3.Connection, raw_text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in raw_text.splitlines() if line.strip()]
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not lines:
        return {
            "status": "error",
            "errors": [{"level": 1, "message": "데이터가 없습니다.", "row": 0}],
            "warnings": [],
            "preview": {"headers": [], "rows": []},
            "parsed_rows": [],
        }

    material_rows = connection.execute(
        """
        SELECT m.id, m.name, m.unit_type, m.unit, m.color_group, m.category, a.alias_name
        FROM materials m
        LEFT JOIN material_aliases a ON a.material_id = m.id
        WHERE m.is_active = 1
        """
    ).fetchall()

    used_material_ids: set[int] = set()
    used_rows = connection.execute(
        "SELECT DISTINCT material_id FROM recipe_items"
    ).fetchall()
    for r in used_rows:
        used_material_ids.add(int(r["material_id"]))

    token_to_material: dict[str, dict[str, Any]] = {}
    for row in material_rows:
        base_payload = {
            "id": row["id"],
            "name": row["name"],
            "unit_type": row["unit_type"],
            "unit": row["unit"],
            "color_group": row["color_group"],
            "category": row["category"],
        }
        token_to_material[normalize_token(row["name"])] = base_payload
        if row["alias_name"]:
            token_to_material[normalize_token(row["alias_name"])] = base_payload

    field_map = {
        "product_name": ["반제품명", "제품명", "레시피명", "PRODUCTNAME", "PRODUCT"],
        "position": ["위치", "POSITION"],
        "ink_name": ["잉크명", "INKNAME"],
    }
    field_tokens = {
        key: {normalize_token(candidate) for candidate in candidates}
        for key, candidates in field_map.items()
    }
    remark_tokens = {normalize_token(c) for c in ["비고", "REMARK", "NOTE"]}

    def get_header_config(row_cells, current_row_index, previous_config):
        normalized_headers = [normalize_token(c) for c in row_cells]

        explicit_indexes: dict[str, int] = {}
        for key, norm_cand in field_tokens.items():
            idx = next((i for i, val in enumerate(normalized_headers) if val in norm_cand), -1)
            if idx >= 0:
                explicit_indexes[key] = idx

        if not explicit_indexes:
            return None

        field_indexes = dict(explicit_indexes)
        prev_fields = previous_config["field_indexes"] if previous_config else {}

        if "product_name" not in field_indexes:
            if "product_name" in prev_fields:
                field_indexes["product_name"] = prev_fields["product_name"]
            elif "position" in field_indexes and field_indexes["position"] > 0:
                field_indexes["product_name"] = field_indexes["position"] - 1
            elif "ink_name" in field_indexes and field_indexes["ink_name"] >= 2:
                field_indexes["product_name"] = field_indexes["ink_name"] - 2

        if "position" not in field_indexes and "position" in prev_fields:
            field_indexes["position"] = prev_fields["position"]

        if "ink_name" not in field_indexes and "ink_name" in prev_fields:
            field_indexes["ink_name"] = prev_fields["ink_name"]

        if "product_name" not in field_indexes:
            return None

        if len(set(field_indexes.values())) < len(field_indexes):
            return None

        field_index_set = set(field_indexes.values())
        metadata_end_index = max(field_index_set)
        trailing_non_empty = [
            idx for idx, value in enumerate(row_cells)
            if idx > metadata_end_index and value.strip()
        ]

        explicit_count = len(explicit_indexes)
        has_product_header = "product_name" in explicit_indexes

        is_header = False
        if has_product_header and trailing_non_empty:
            is_header = True
        elif explicit_count >= 2 and trailing_non_empty:
            is_header = True

        if not is_header:
            return None

        mat_cols = []
        header_warnings = []

        remark_index = next(
            (i for i, val in enumerate(row_cells)
             if i > metadata_end_index and normalize_token(val) in remark_tokens),
            -1,
        )

        for idx, header in enumerate(row_cells):
            if idx in field_index_set:
                continue
            if idx <= metadata_end_index:
                continue
            if idx == remark_index:
                continue
            if not header.strip():
                continue

            token = normalize_token(header)
            mat = token_to_material.get(token)

            if not mat:
                mat = _auto_register_material(connection, header.strip())
                token_to_material[token] = mat
                header_warnings.append({"level": 3, "message": f"새 원재료를 자동 등록했습니다: {header.strip()}", "row": current_row_index})
            elif mat["id"] not in used_material_ids:
                header_warnings.append({"level": 3, "message": f"처음 사용하는 원재료입니다: {mat['name']} — 맞는지 확인해 주세요.", "row": current_row_index})

            mat_cols.append({"index": idx, "header": header, "material": mat})

        return {
            "field_indexes": field_indexes,
            "remark_index": remark_index,
            "material_columns": mat_cols,
            "warnings": header_warnings,
            "headers": row_cells,
            "seed_values": {
                key: row_cells[index].strip()
                for key, index in field_indexes.items()
                if index < len(row_cells)
                and row_cells[index].strip()
                and normalize_token(row_cells[index]) not in field_tokens[key]
            },
            "reset_carry": "product_name" in explicit_indexes,
        }

    parsed_rows = []
    preview_rows = []

    current_config = None
    last_product_name = ""
    last_position = ""
    last_ink_name = ""
    global_headers: list[str] = []

    for row_index, line in enumerate(lines, start=1):
        cells = [cell.strip() for cell in line.split("\t")]

        new_config = get_header_config(cells, row_index, current_config)
        if new_config:
            current_config = new_config
            if new_config["reset_carry"]:
                last_product_name = ""
                last_position = ""
                last_ink_name = ""
            if new_config["seed_values"].get("product_name"):
                last_product_name = new_config["seed_values"]["product_name"].strip()
            if new_config["seed_values"].get("position"):
                last_position = new_config["seed_values"]["position"].strip()
            if new_config["seed_values"].get("ink_name"):
                last_ink_name = new_config["seed_values"]["ink_name"].strip()
            warnings.extend(new_config["warnings"])
            if not global_headers:
                global_headers = new_config["headers"]
            continue

        if not current_config:
            continue

        field_idx = current_config["field_indexes"]
        prod_idx = field_idx["product_name"]
        pos_idx = field_idx.get("position")
        ink_idx = field_idx.get("ink_name")
        prod = cells[prod_idx] if prod_idx < len(cells) else ""
        pos = cells[pos_idx] if pos_idx is not None and pos_idx < len(cells) else ""
        ink = cells[ink_idx] if ink_idx is not None and ink_idx < len(cells) else ""

        if not prod:
            prod = last_product_name
        else:
            last_product_name = prod

        if pos_idx is not None and not pos:
            pos = last_position
        elif pos:
            last_position = pos

        if ink_idx is not None and not ink:
            ink = last_ink_name
        elif ink:
            last_ink_name = ink

        if not prod:
            errors.append({"level": 2, "message": "반제품명이 누락되었습니다.", "row": row_index})

        row_items = []
        preview_items = []

        for col in current_config["material_columns"]:
            idx = col["index"]
            raw_val = cells[idx] if idx < len(cells) else ""
            if not raw_val:
                continue

            mat = col["material"]
            numeric_value, text_value = _parse_value(raw_val)

            if numeric_value is None and text_value is None:
                continue  # "-" or empty

            row_items.append({
                "material_id": mat["id"],
                "value_weight": numeric_value,
                "value_text": text_value,
            })

            display = raw_val.strip()
            preview_items.append({
                "material_id": mat["id"],
                "material_name": mat["name"],
                "value": numeric_value if numeric_value is not None and text_value is None else display,
            })

        if not row_items:
            continue

        remark_idx = current_config.get("remark_index", -1)
        remark_val = (
            cells[remark_idx].strip()
            if 0 <= remark_idx < len(cells) and cells[remark_idx].strip()
            else None
        )

        parsed_rows.append({
            "product_name": prod or "(미입력)",
            "position": pos or None,
            "ink_name": ink or None,
            "remark": remark_val,
            "items": row_items,
        })

        preview_rows.append({
            "product_name": prod or "(미입력)",
            "position": pos or None,
            "ink_name": ink or None,
            "remark": remark_val,
            "items": preview_items,
        })

    if not global_headers:
        errors.append({"level": 1, "message": "유효한 헤더를 찾을 수 없습니다.", "row": 0})

    if not errors and not parsed_rows:
        errors.append({"level": 1, "message": "파싱 가능한 데이터가 없습니다.", "row": 0})
    parse_status = "error" if errors else "ok"
    return {
        "status": parse_status,
        "errors": errors,
        "warnings": warnings,
        "preview": {"headers": global_headers, "rows": preview_rows},
        "parsed_rows": parsed_rows,
    }
