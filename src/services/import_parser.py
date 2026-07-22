import difflib
import sqlite3
from typing import Any

from ..db import normalize_token
from .cell_value_parser import parse_cell


# item-code P3 — 유사 후보 추천(difflib) cutoff. match_item_codes.py(P2) 값과 동일.
_CLOSE_MATCH_CUTOFF = 0.75
_CLOSE_MATCH_N = 3


def _load_master_index(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """item_code_master 를 kind 별로 정규화 인덱스화.

    반환 구조:
        {"by_kind": {kind: {norm_name: [code,...]}},
         "rows":    {code: {name, category_hint, kind}},
         "norm_names": {kind: [norm_name, ...]},   # 유사 후보 비교 풀
         "names":   {norm_name: 원문이름}}           # 표시용

    마스터가 0행이면 None 을 반환한다. 이는 "하위호환 모드" — 마스터 미이관 환경에서는
    3단 판정 전부를 unknown 으로 취급하되 차단하지 않고 기존 동작을 그대로 유지한다(spec §0).
    match_item_codes.py 의 _build_master_index 와 동일 구조지만 import 경로 의존을 피하려
    여기에 둔다(tools/ 모듈에 의존하지 않는다).
    """
    rows = connection.execute(
        "SELECT code, name, kind, category_hint FROM item_code_master"
    ).fetchall()
    if not rows:
        return None

    by_kind: dict[str, dict[str, list[str]]] = {"material": {}, "product": {}}
    table_rows: dict[str, dict[str, Any]] = {}
    norm_names: dict[str, list[str]] = {"material": [], "product": []}
    names: dict[str, str] = {}
    for r in rows:
        code = r["code"]
        kind = r["kind"]
        norm = normalize_token(r["name"])
        by_kind.setdefault(kind, {})
        by_kind[kind].setdefault(norm, []).append(code)
        if norm and norm not in names:
            names[norm] = r["name"]
        if norm and norm not in norm_names.get(kind, []):
            norm_names.setdefault(kind, []).append(norm)
        table_rows[code] = {
            "name": r["name"],
            "category_hint": r["category_hint"],
            "kind": kind,
        }
    return {
        "by_kind": by_kind,
        "rows": table_rows,
        "norm_names": norm_names,
        "names": names,
    }


def _similar_candidates(token: str, master_index: dict[str, Any] | None, limit: int = _CLOSE_MATCH_N) -> list[str]:
    """마스터 정규화명 풀에서 유사 후보를 '원문이름(코드)' 형태로 최대 limit 건 반환.

    master_index 가 None(마스터 0행, 하위호환 모드)이면 빈 목록을 반환한다.
    """
    if not token or master_index is None:
        return []
    pool = master_index["norm_names"].get("material", []) + master_index["norm_names"].get("product", [])
    # 중복 제거하되 순서 유지
    seen: set[str] = set()
    deduped: list[str] = []
    for n in pool:
        if n and n not in seen:
            seen.add(n)
            deduped.append(n)
    close = difflib.get_close_matches(token, deduped, n=limit, cutoff=_CLOSE_MATCH_CUTOFF)
    # '카본블랙(AS00xx)' 형태 — 원문이름 + 대표 코드
    out: list[str] = []
    for norm in close:
        display_name = master_index["names"].get(norm, norm)
        codes = (
            master_index["by_kind"].get("material", {}).get(norm, [])
            + master_index["by_kind"].get("product", {}).get(norm, [])
        )
        suffix = f"({codes[0]})" if codes else ""
        out.append(f"{display_name}{suffix}")
    return out


def _auto_register_material(
    connection: sqlite3.Connection, name: str, code: str | None = None
) -> dict[str, Any]:
    """미등록 자재를 INSERT 하고 payload 반환. code 가 주어지면(마스터 매칭) materials.code 채움.

    item-code P3: status=master 판정 시 호출부가 마스터 코드를 넘겨 자동 부여한다.
    """
    cursor = connection.execute(
        """
        INSERT INTO materials (name, unit_type, unit, color_group, category, is_active, code)
        VALUES (?, 'weight', 'g', 'none', '미분류', 1, ?)
        """,
        (name, code),
    )
    return {
        "id": cursor.lastrowid,
        "name": name,
        "unit_type": "weight",
        "unit": "g",
        "color_group": "none",
        "category": "미분류",
        "code": code,
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


def parse_import_text(
    connection: sqlite3.Connection,
    raw_text: str,
) -> dict[str, Any]:
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
            "material_matches": [],
        }

    material_rows = connection.execute(
        """
        SELECT m.id, m.name, m.unit_type, m.unit, m.color_group, m.category, m.code, a.alias_name
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
            "code": row["code"],
        }
        token_to_material[normalize_token(row["name"])] = base_payload
        if row["alias_name"]:
            token_to_material[normalize_token(row["alias_name"])] = base_payload

    # item-code P3: 마스터 인덱스 로드. 비어 있으면 None → 하위호환 모드(차단 없음).
    master_index = _load_master_index(connection)
    # 1차→2차 연계 자재 인식용 — completed 레시피 product_name 의 정규화 집합을 1회 로드.
    # 2차 레시피의 자재 중 마스터에 없어도 우리가 만드는 1차 반제품(레시피 product_name)이면
    # 정상 자재로 인식한다(unknown 차단 우회). 품명이 같은 완료 레시피가 하나라도 있으면 매칭.
    completed_recipe_names: set[str] = set()
    try:
        for row in connection.execute(
            "SELECT product_name FROM recipes WHERE status = 'completed'"
        ).fetchall():
            name = row["product_name"]
            if name not in (None, ""):
                completed_recipe_names.add(normalize_token(str(name)))
    except sqlite3.OperationalError:
        completed_recipe_names = set()  # recipes 테이블 없는 구버전/테스트 스키마
    # 자재 3단 판정 결과를 preview 응답에 모아 담는다(spec §1 material_matches).
    material_matches: list[dict[str, Any]] = []

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
    # 공정 설명 열: 자재가 아니라 그 위치의 안내문("개시제 교반 - 300rpm" 등).
    # 열 제목이 '설명'이면(여러 번 가능) 해당 칸 내용이 자재 사이 설명 줄이 된다.
    step_tokens = {normalize_token(c) for c in ["설명", "공정", "STEP"]}

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
        # '설명' 열들 — 자재 자동 등록 대상에서 제외하고 위치만 기억
        step_indexes = [
            i for i, val in enumerate(row_cells)
            if i > metadata_end_index and i != remark_index
            and normalize_token(val) in step_tokens
        ]

        for idx, header in enumerate(row_cells):
            if idx in field_index_set:
                continue
            if idx <= metadata_end_index:
                continue
            if idx == remark_index:
                continue
            if idx in step_indexes:
                continue
            if not header.strip():
                continue

            token = normalize_token(header)
            mat = token_to_material.get(token)

            if not mat:
                # item-code P3: 3단 판정. 마스터가 비어 있지 않을 때만 차단 로직이 동작.
                # master_index 가 None(마스터 0행)이면 기존 동작(코드 없이 자동 등록 + 경고)을
                # 그대로 유지한다 — 하위호환(spec §0).
                master_code: str | None = None
                if master_index is not None:
                    # 1순위: kind='material' 마스터. 단일 히트면 code 부여, 다중 히트면 unknown 취급.
                    codes = master_index["by_kind"].get("material", {}).get(token, [])
                    if not codes:
                        # 2순위: kind='product' 마스터(반제품을 원료로 쓰는 자재, 예: PB→B0020).
                        codes = master_index["by_kind"].get("product", {}).get(token, [])
                    if len(codes) == 1:
                        master_code = codes[0]

                similar: list[str] = []
                if master_code:
                    # status=master: 마스터에만 존재 → 자동 등록 + 코드 부여(경고 아님, 안내).
                    mat = _auto_register_material(connection, header.strip(), code=master_code)
                    token_to_material[token] = mat
                    header_warnings.append({
                        "level": 3,
                        "message": f"마스터 품목 자동 등록: {header.strip()} ({master_code})",
                        "row": current_row_index,
                    })
                    material_matches.append({
                        "name": header.strip(), "status": "master",
                        "code": master_code, "similar": [],
                    })
                else:
                    # status=unknown: 어디에도 없음. 유사 후보(기존 materials + 마스터 양쪽).
                    similar = _similar_candidates(token, master_index)
                    # 1차→2차 연계 자재 인식(unknown 차단보다 먼저) — 마스터에 없어도 우리가 만드는
                    # 1차 반제품(completed 레시피 product_name)이면 정상 자재로 인식한다. 코드 없이
                    # 자동 등록하고 안내(level-3)만 남긴다 — 마스터에 없어도 정상 매칭.
                    if token in completed_recipe_names:
                        mat = _auto_register_material(connection, header.strip())
                        token_to_material[token] = mat
                        header_warnings.append({
                            "level": 3,
                            "message": f"자체 제조 반제품(1차) 연계: {header.strip()}",
                            "row": current_row_index,
                        })
                        material_matches.append({
                            "name": header.strip(), "status": "recipe",
                            "code": None, "similar": [],
                        })
                    else:
                        # 마스터가 비어 있으면(하위호환) 차단하지 않고 기존처럼 코드 없이 자동 등록.
                        if master_index is None:
                            mat = _auto_register_material(connection, header.strip())
                            token_to_material[token] = mat
                            header_warnings.append({
                                "level": 3,
                                "message": f"새 원재료가 자동 등록됩니다: {header.strip()}",
                                "row": current_row_index,
                            })
                        else:
                            # 기본 차단: errors 에 추가 → 기존 UI 규칙상 등록 버튼 비활성.
                            similar_txt = f" — 유사: {', '.join(similar)}" if similar else ""
                            errors.append({
                                "level": 2,
                                "message": f"마스터에 없는 품목: {header.strip()}{similar_txt} "
                                "(확인 후 등록하려면 신규 품목 확인 필요)",
                                "row": current_row_index,
                            })
                            # 차단한 자재도 파싱은 진행(preview 표시용)하되 등록은 막은 상태.
                            # parsed["errors"] 가 비어있지 않으면 import 엔드포인트가 400 로 거부한다.
                            mat = _auto_register_material(connection, header.strip())
                            token_to_material[token] = mat
                        material_matches.append({
                            "name": header.strip(), "status": "unknown",
                            "code": None, "similar": similar,
                        })
            else:
                # status=existing: materials 에 존재 → 기존 material_id. code 있으면 함께 표시.
                material_matches.append({
                    "name": header.strip(),
                    "status": "existing",
                    "code": mat.get("code"),
                    "similar": [],
                })
                if mat["id"] not in used_material_ids:
                    header_warnings.append({"level": 3, "message": f"처음 사용하는 원재료입니다: {mat['name']} — 맞는지 확인해 주세요.", "row": current_row_index})

            mat_cols.append({"index": idx, "header": header, "material": mat})

        return {
            "field_indexes": field_indexes,
            "remark_index": remark_index,
            "step_indexes": step_indexes,
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
        row_steps = []  # 공정 설명: {position(앞선 자재 수), note}

        # 자재·설명 열을 시트 열 순서대로 함께 훑어 설명 위치(몇 번째 자재 뒤)를 계산
        merged_cols = sorted(
            [("material", col) for col in current_config["material_columns"]]
            + [("step", {"index": i}) for i in current_config.get("step_indexes", [])],
            key=lambda pair: pair[1]["index"],
        )
        for kind, col in merged_cols:
            idx = col["index"]
            raw_val = cells[idx] if idx < len(cells) else ""
            if not raw_val:
                continue

            if kind == "step":
                row_steps.append({"position": len(row_items), "note": raw_val.strip()})
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
            "steps": row_steps,
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
        "material_matches": material_matches,
    }
