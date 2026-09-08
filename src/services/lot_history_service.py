"""자재 LOT 이력 — 레시피(가족)·자재 축으로 LOT 교체 시점을 읽어 내는 순방향 조회.

`trace_material_lot`(blend_service) 이 "이 LOT 이 어디 들어갔나"(역추적)라면, 여기는
"이 레시피의 이 자재는 언제 어떤 LOT 으로 바뀌었나"(순방향)를 답한다. 새 저장소 없이
blend_details.material_lot 스냅샷을 시간순으로 세워 이전 배치와 다른 지점만 뽑는다.

가족(family)
    레시피는 개정되면 id 가 바뀌고(revision_of 체인), 계보 도구로 이름이 다른 레시피끼리
    묶이기도 한다(NPR→NPR-S2→NPR-S). recipe_id 로만 묶으면 개정 시점에 이력이 끊기므로
    체인 root 를 가족 키로 쓴다. 레시피 연결이 없는 기록(구 프로그램 이관 등)은 제품명이
    어느 체인 구성원의 이름과 같으면 그 가족에 붙이고, 아니면 제품명 자체를 가족으로 둔다.

판정 규칙
    - 완료(completed) 기록만. 취소분 LOT 을 교체로 잡으면 오탐.
    - 정렬은 work_date, id (같은 날 N로트는 저장 순).
    - LOT 비교는 공백 정리·대소문자 무시. 빈 LOT 은 '미입력' 구간으로 보이되 교체 사건은
      아니다 — X→(빈)→Y 는 Y 시점에 X→Y 한 번으로 센다.
    - A→B→A 되돌아감은 두 번의 교체로 그대로 남긴다(혼용 시기 파악용).
    - 자재는 품목코드 우선, 없으면 자재명(정규화)으로 묶는다.
    - 기간 필터는 계산이 끝난 뒤에 건다 — 기간 첫 기록을 교체로 오인하지 않도록
      가족 전체 이력을 먼저 세운다.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

_WS = re.compile(r"\s+")


def norm_lot(value: Any) -> str | None:
    """LOT 비교 키. 빈 값은 None."""
    if value is None:
        return None
    text = _WS.sub(" ", str(value)).strip()
    return text.upper() if text else None


def _norm_name(value: Any) -> str:
    return _WS.sub(" ", str(value or "")).strip().casefold()


def _family_maps(connection: sqlite3.Connection) -> tuple[dict[int, int], dict[int, dict[str, Any]], dict[str, int]]:
    """recipe_id→root, root→가족 정보, 정규화 제품명→root.

    root 의 표시 이름은 체인 안의 활성(비취소·비초안) 최신본 이름 — 현재 화면에서 부르는
    이름으로 보여야 찾기 쉽다. 활성본이 없으면 마지막 id 의 이름.
    """
    rows = connection.execute(
        """
        WITH RECURSIVE up(id, root, parent, depth) AS (
            SELECT id, id, revision_of, 0 FROM recipes
            UNION ALL
            SELECT up.id, r.id, r.revision_of, up.depth + 1
            FROM up JOIN recipes r ON r.id = up.parent
            WHERE up.depth < 100
        ),
        roots AS (
            SELECT id, root FROM up WHERE parent IS NULL
        )
        SELECT r.id, roots.root, r.product_name, r.status, r.category
        FROM recipes r JOIN roots ON roots.id = r.id
        ORDER BY r.id
        """
    ).fetchall()
    recipe_root: dict[int, int] = {}
    families: dict[int, dict[str, Any]] = {}
    name_root: dict[str, int] = {}
    for row in rows:
        rid, root = int(row["id"]), int(row["root"])
        recipe_root[rid] = root
        fam = families.setdefault(root, {
            "key": f"r:{root}", "label": row["product_name"], "label_id": rid,
            "active_label_id": None, "recipe_ids": [], "names": set(), "category": row["category"],
        })
        fam["recipe_ids"].append(rid)
        fam["names"].add(row["product_name"])
        if row["status"] not in ("canceled", "draft"):
            fam["label"], fam["active_label_id"], fam["category"] = row["product_name"], rid, row["category"]
        elif fam["active_label_id"] is None:
            fam["label"], fam["label_id"] = row["product_name"], rid
        name_root.setdefault(_norm_name(row["product_name"]), root)
    return recipe_root, families, name_root


def _fetch_detail_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT r.id AS record_id, r.recipe_id, r.product_name, r.product_lot,
               r.work_date, r.worker,
               d.material_code, d.material_name, d.material_lot, d.sequence_order
        FROM blend_details d
        JOIN blend_records r ON r.id = d.blend_record_id
        WHERE r.status = 'completed'
        ORDER BY r.work_date, r.id, d.sequence_order, d.id
        """
    ).fetchall()


def _resolve_family(row: sqlite3.Row, recipe_root, families, name_root) -> tuple[str, str]:
    """기록 한 건의 (가족 키, 표시 이름)."""
    rid = row["recipe_id"]
    if rid is not None and int(rid) in recipe_root:
        fam = families[recipe_root[int(rid)]]
        return fam["key"], fam["label"]
    root = name_root.get(_norm_name(row["product_name"]))
    if root is not None:
        fam = families[root]
        return fam["key"], fam["label"]
    name = str(row["product_name"] or "").strip()
    return f"p:{name}", name


def _material_key(row: sqlite3.Row) -> str:
    code = (row["material_code"] or "").strip()
    return f"c:{code}" if code else f"n:{_norm_name(row['material_name'])}"


def list_families(connection: sqlite3.Connection) -> dict[str, Any]:
    """화면 선택지 — 배합 기록이 있는 가족과 그 안에서 쓰인 자재 목록.

    기록이 없는 레시피는 이력이 없으니 목록에 넣지 않는다.
    """
    recipe_root, families, name_root = _family_maps(connection)
    out: dict[str, dict[str, Any]] = {}
    materials_all: dict[str, str] = {}
    for row in _fetch_detail_rows(connection):
        key, label = _resolve_family(row, recipe_root, families, name_root)
        fam = out.setdefault(key, {
            "key": key, "label": label, "record_ids": set(), "materials": {},
            "first_date": row["work_date"], "last_date": row["work_date"],
        })
        fam["record_ids"].add(int(row["record_id"]))
        fam["last_date"] = max(fam["last_date"], row["work_date"])
        fam["first_date"] = min(fam["first_date"], row["work_date"])
        mkey = _material_key(row)
        fam["materials"][mkey] = row["material_name"]
        materials_all[mkey] = row["material_name"]
    items = []
    for fam in out.values():
        items.append({
            "key": fam["key"], "label": fam["label"],
            "record_count": len(fam["record_ids"]),
            "first_date": fam["first_date"], "last_date": fam["last_date"],
            "materials": [
                {"key": k, "name": v} for k, v in sorted(fam["materials"].items(), key=lambda kv: kv[1])
            ],
        })
    items.sort(key=lambda f: (f["last_date"], f["record_count"]), reverse=True)
    return {
        "items": items,
        "materials": [
            {"key": k, "name": v} for k, v in sorted(materials_all.items(), key=lambda kv: kv[1])
        ],
    }


def lot_history(
    connection: sqlite3.Connection,
    *,
    family: str | None = None,
    material: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """가족·자재 축의 LOT 구간(타임라인)과 교체 사건 목록.

    family: list_families 의 key("r:<root>" 또는 "p:<제품명>"). material: 자재 key
    ("c:<품목코드>"/"n:<자재명>") 또는 자재명 그대로. 둘 중 하나는 있어야 한다 — 둘 다
    비우면 전 기록을 훑는 셈이라 ValueError.
    """
    family = (family or "").strip() or None
    material = (material or "").strip() or None
    if not family and not material:
        raise ValueError("레시피 또는 자재 중 하나는 지정해야 합니다.")
    start_date = (start_date or "").strip() or None
    end_date = (end_date or "").strip() or None

    recipe_root, families, name_root = _family_maps(connection)
    material_keys: set[str] | None = None
    if material:
        material_keys = {material} if material[:2] in ("c:", "n:") else {f"n:{_norm_name(material)}"}

    # (가족, 자재) 그룹별로 시간순 상세 행을 모은다.
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _fetch_detail_rows(connection):
        fkey, flabel = _resolve_family(row, recipe_root, families, name_root)
        if family and fkey != family:
            continue
        mkey = _material_key(row)
        if material_keys is not None and mkey not in material_keys:
            # 자재명으로 들어온 경우 품목코드가 붙은 행도 이름으로 맞춰 준다.
            if not (material and f"n:{_norm_name(row['material_name'])}" in material_keys):
                continue
        grp = groups.setdefault((fkey, mkey), {
            "family_key": fkey, "family_label": flabel, "material_key": mkey,
            "material_name": row["material_name"], "material_code": row["material_code"],
            "rows": [],
        })
        grp["material_name"] = row["material_name"]      # 최신 표기(이름 정리 반영)
        if row["material_code"]:
            grp["material_code"] = row["material_code"]
        grp["rows"].append(row)

    def in_range(date: str) -> bool:
        if start_date and date < start_date:
            return False
        if end_date and date > end_date:
            return False
        return True

    rows_out: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    record_ids: set[int] = set()
    for grp in groups.values():
        segments: list[dict[str, Any]] = []
        prev_key: str | None = None
        prev_display: str | None = None
        seen_records: set[int] = set()
        for row in grp["rows"]:
            key = norm_lot(row["material_lot"])
            display = str(row["material_lot"]).strip() if key else None
            rec_id = int(row["record_id"])
            if segments and segments[-1]["lot_key"] == key:
                seg = segments[-1]
                seg["last_date"], seg["last_product_lot"], seg["last_record_id"] = (
                    row["work_date"], row["product_lot"], rec_id)
                seg["_records"].add(rec_id)
            else:
                segments.append({
                    "lot": display, "lot_key": key,
                    "first_date": row["work_date"], "first_product_lot": row["product_lot"],
                    "first_record_id": rec_id,
                    "last_date": row["work_date"], "last_product_lot": row["product_lot"],
                    "last_record_id": rec_id,
                    "_records": {rec_id},
                })
            if key is not None:
                if prev_key is not None and key != prev_key:
                    changes.append({
                        "work_date": row["work_date"], "record_id": rec_id,
                        "product_lot": row["product_lot"], "product_name": row["product_name"],
                        "family_key": grp["family_key"], "family_label": grp["family_label"],
                        "material_key": grp["material_key"], "material_name": grp["material_name"],
                        "prev_lot": prev_display, "new_lot": display, "worker": row["worker"],
                    })
                prev_key, prev_display = key, display
            if in_range(row["work_date"]):
                seen_records.add(rec_id)
        # 기간에 걸치는 구간만 남기되 실제 시작·끝 날짜는 자르지 않는다("언제부터 썼나"가 정보).
        kept = []
        for seg in segments:
            if start_date and seg["last_date"] < start_date:
                continue
            if end_date and seg["first_date"] > end_date:
                continue
            seg["record_count"] = len(seg.pop("_records"))
            kept.append(seg)
        if not kept and not seen_records:
            continue
        record_ids |= seen_records
        rows_out.append({
            "family_key": grp["family_key"], "family_label": grp["family_label"],
            "material_key": grp["material_key"], "material_name": grp["material_name"],
            "material_code": grp["material_code"],
            "segments": kept,
            "change_count": sum(
                1 for c in changes
                if c["family_key"] == grp["family_key"] and c["material_key"] == grp["material_key"]
                and in_range(c["work_date"])
            ),
            "record_count": len(seen_records),
        })

    changes = [c for c in changes if in_range(c["work_date"])]
    changes.sort(key=lambda c: (c["work_date"], c["record_id"]), reverse=True)
    if family:
        rows_out.sort(key=lambda r: (-r["change_count"], r["material_name"]))
    else:
        rows_out.sort(key=lambda r: (r["family_label"], r["material_name"]))

    family_info = None
    if family:
        if family.startswith("r:"):
            fam = families.get(int(family[2:])) if family[2:].isdigit() else None
            if fam:
                family_info = {"key": family, "label": fam["label"], "recipe_ids": fam["recipe_ids"]}
        if family_info is None:
            family_info = {"key": family, "label": family[2:] if family.startswith("p:") else family,
                           "recipe_ids": []}

    return {
        "family": family_info,
        "material": material,
        "start_date": start_date,
        "end_date": end_date,
        "record_count": len(record_ids),
        "rows": rows_out,
        "changes": changes,
        "change_count": len(changes),
    }
