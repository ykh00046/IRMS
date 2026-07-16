"""ERP 품목코드 자동 매칭 스크립트(item-code P2).

보고서 우선(report-first). 기본 실행은 읽기 전용 매칭 보고서만 출력하고, ``--apply`` 를
줘야 '확정 후보(1건 히트)'만 DB 에 반영한다. 모호(2건 이상)·미매칭은 항상 보고만 한다.

설계: docs/01-plan/features/item-code.plan.md §2·§7. P1 산출물(item_code_master,
materials.code, recipes.product_code)을 전제로 동작.

매칭 규칙(요약 — spec 참고):
  1) 자재: materials(is_active=1, code IS NULL) 대상. 키 = normalize(name) + 각
     normalize(alias_name).
       - 1순위 kind='material' 마스터 검색.
       - 2순위(1순위 0건) kind='product' 마스터 검색 — 반제품을 원료로 쓰는 자재
         (PB→B0020). '반제품 코드 매칭'으로 구분 표기.
       - 히트 1건 → 확정, 2건+ → 모호(보고만), 0건 → difflib 유사 후보 최대 2건.
  2) 레시피: recipes(status='completed', product_code IS NULL) 의 DISTINCT
     product_name. kind='product' 마스터 검색.
       - 확정 시 같은 product_name 의 completed 행 전체(개정 체인)에 product_code 부여.
       - category 가 NULL → 마스터 category_hint 로 채움 후보. 기존 category != hint 면
         '분류 충돌' 보고만(덮지 않음).
       - 모호/미매칭은 자재와 동일.

사용:
  python tools/match_item_codes.py                  # 보고서만
  python tools/match_item_codes.py --apply          # 확정 후보 반영
  python tools/match_item_codes.py --db 경로/rehearsal.db --apply
"""

from __future__ import annotations

import argparse
import difflib
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_connection, init_db  # noqa: E402  (sys.path 조정 후 import)
from src.db.migrations import apply_schema_migrations  # noqa: E402
from src.db.queries import normalize_token  # noqa: E402


# 유사 후보 추천 시 difflib cutoff. 운영 리허설(§7)에서 변형명(SBCT-1→BC2000 계열)을
# 수동 확정 후보로 띄우기 위한 임계치 — 너무 낮으면 잡음, 너무 높으면 변형명 누락.
_CLOSE_MATCH_CUTOFF = 0.75
_CLOSE_MATCH_N = 2


def _open_target_db(db_arg: str | None) -> tuple[sqlite3.Connection, str]:
    """대상 DB 연결 반환. 명시 --db(비관례 파일명 포함) 시 그 파일에 직접 연결하되
    스키마 마이그레이션을 *같은 연결*에 적용한다(운영 스냅샷 복사본 등 pre-P1 DB 대응).

    반환: (connection, 경로표시문자열). 관례 경로(--db 미지정 또는 irms.db) 는
    get_connection() 을 쓰고, 비관례 파일명은 sqlite3 직접 연결 후 apply_schema_migrations.
    """
    if not db_arg:
        # 관례 개발 DB — init_db 가 스키마(item_code_master 포함) 보장.
        init_db()
        return get_connection(), "(기본 개발 DB)"

    db_file = os.path.abspath(db_arg)
    # 관례 파일명(irms.db): 환경변수 경로로 잡고 init_db/get_connection 사용.
    if os.path.basename(db_file) == "irms.db":
        os.environ["IRMS_DATA_DIR"] = str(os.path.dirname(db_file))
        init_db()
        return get_connection(), db_file

    # 비관례 파일명(예: rehearsal.db): 직접 연결 후 *같은 연결*에 마이그레이션 적용.
    # init_db() 가 관례 DB 에만 스키마를 잡으므로, 대상 파일에는 수동으로 컬럼/마스터
    # 테이블을 보장해야 한다(import_item_codes.py 의 비관례 경로 분기와 동일 목적).
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema_migrations(conn)
    return conn, db_file


# ---------- 마스터 정규화 인덱스 ----------


def _build_master_index(conn: sqlite3.Connection) -> dict:
    """item_code_master 를 kind 별로 (normalize(name) → [code, ...]) 인덱스화.

    반환: {'by_kind': {kind: {norm: [code,...]}}, 'names': {norm: name 원문},
           'rows': {code: {name, category_hint, kind, ...}}}
    같은 정규화명에 코드가 여러 개면 모호(ambiguous) 판정용으로 리스트로 누적.
    """
    by_kind: dict[str, dict[str, list[str]]] = {"material": defaultdict(list),
                                                "product": defaultdict(list)}
    names: dict[str, str] = {}      # norm(name) → 대표 원문 이름(표시용)
    rows: dict[str, dict] = {}      # code → 마스터 행
    norm_names: dict[str, list[str]] = {"material": [], "product": []}  # 유사도 비교 풀

    for r in conn.execute(
        "SELECT code, name, category_hint, kind, spec, source FROM item_code_master"
    ).fetchall():
        code = r["code"]
        kind = r["kind"]
        name = r["name"]
        norm = normalize_token(name)
        by_kind[kind][norm].append(code)
        names.setdefault(norm, name)
        rows[code] = {"name": name, "category_hint": r["category_hint"],
                      "kind": kind, "spec": r["spec"], "source": r["source"]}
        norm_names[kind].append(norm)

    return {
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "names": names,
        "rows": rows,
        "norm_names": norm_names,
    }


def _close_matches(query_norm: str, pool: list[str]) -> list[str]:
    """difflib 으로 pool 내 정규화명 중 유사 후보 최대 N개 반환."""
    return difflib.get_close_matches(query_norm, pool, n=_CLOSE_MATCH_N,
                                     cutoff=_CLOSE_MATCH_CUTOFF)


def _collect_hits(keys: list[str], idx: dict[str, list[str]]) -> list[str]:
    """정규화 키 목록으로 인덱스 조회 → 중복 제거된 코드 목록(순서 유지)."""
    hits: list[str] = []
    seen: set[str] = set()
    for k in keys:
        for code in idx.get(k, []):
            if code not in seen:
                seen.add(code)
                hits.append(code)
    return hits


# ---------- 자재 매칭 ----------


def _load_materials_targets(conn: sqlite3.Connection) -> list[dict]:
    """매칭 대상 자재: is_active=1 AND code IS NULL. 이름 + 별칭(normalize) 키 묶음."""
    rows = conn.execute(
        "SELECT id, name FROM materials WHERE is_active=1 AND code IS NULL ORDER BY id"
    ).fetchall()
    aliases_by_mid: dict[int, list[str]] = defaultdict(list)
    for a in conn.execute(
        "SELECT material_id, alias_name FROM material_aliases"
    ).fetchall():
        aliases_by_mid[a["material_id"]].append(a["alias_name"])

    targets = []
    for r in rows:
        keys = [normalize_token(r["name"])] + [
            normalize_token(a) for a in aliases_by_mid.get(r["id"], [])
        ]
        targets.append({"id": r["id"], "name": r["name"],
                        "keys": [k for k in keys if k]})
    return targets


def match_materials(conn: sqlite3.Connection, index: dict) -> dict:
    """자재 매칭 수행. 반환: {confirmed, confirmed_cross, ambiguous, unmatched}."""
    idx_material = index["by_kind"]["material"]
    idx_product = index["by_kind"]["product"]
    pool_material = index["norm_names"]["material"]
    pool_product = index["norm_names"]["product"]
    names = index["names"]

    confirmed = []          # kind='material' 확정
    confirmed_cross = []    # kind='product' 확정(반제품→원료)
    ambiguous = []
    unmatched = []

    for t in _load_materials_targets(conn):
        # 1순위: material 마스터
        hits_mat = _collect_hits(t["keys"], idx_material)
        if hits_mat:
            if len(hits_mat) == 1:
                confirmed.append({"material_id": t["id"], "name": t["name"],
                                  "code": hits_mat[0]})
            else:
                ambiguous.append({"material_id": t["id"], "name": t["name"],
                                  "codes": hits_mat, "matched_kind": "material"})
            continue

        # 2순위: product 마스터(반제품→원료 교차 매칭)
        hits_prod = _collect_hits(t["keys"], idx_product)
        if hits_prod:
            if len(hits_prod) == 1:
                confirmed_cross.append({"material_id": t["id"], "name": t["name"],
                                        "code": hits_prod[0]})
            else:
                ambiguous.append({"material_id": t["id"], "name": t["name"],
                                  "codes": hits_prod, "matched_kind": "product"})
            continue

        # 미매칭: 유사 후보(material 풀 우선, 없으면 product 풀에서)
        query = t["keys"][0] if t["keys"] else ""
        close = _close_matches(query, pool_material)
        if not close:
            close = _close_matches(query, pool_product)
        unmatched.append({"material_id": t["id"], "name": t["name"],
                          "close": [names.get(c, c) for c in close]})

    return {"confirmed": confirmed, "confirmed_cross": confirmed_cross,
            "ambiguous": ambiguous, "unmatched": unmatched}


# ---------- 레시피 매칭 ----------


def _load_recipe_targets(conn: sqlite3.Connection) -> list[dict]:
    """매칭 대상: completed & product_code IS NULL 인 DISTINCT product_name.
    각 이름별 completed 행 id 목록과 현재 category 집합 수집(개정 체인 전체 부여용)."""
    rows = conn.execute(
        "SELECT id, product_name, category FROM recipes "
        "WHERE status='completed' AND product_code IS NULL ORDER BY id"
    ).fetchall()
    by_name: dict[str, dict] = {}
    for r in rows:
        pn = r["product_name"]
        d = by_name.setdefault(pn, {"name": pn, "recipe_ids": [], "categories": set()})
        d["recipe_ids"].append(r["id"])
        if r["category"] is not None:
            d["categories"].add(r["category"])
    return list(by_name.values())


def match_recipes(conn: sqlite3.Connection, index: dict) -> dict:
    """레시피 매칭 수행. 반환: {confirmed, category_conflict, ambiguous, unmatched}."""
    idx_product = index["by_kind"]["product"]
    pool_product = index["norm_names"]["product"]
    names = index["names"]
    rows = index["rows"]

    confirmed = []           # product_code 부여 + category 채움/유지 후보
    category_conflict = []   # 기존 category != hint (보고만, 덮지 않음)
    ambiguous = []
    unmatched = []

    for t in _load_recipe_targets(conn):
        key = normalize_token(t["name"])
        hits = _collect_hits([key], idx_product)
        if hits:
            if len(hits) > 1:
                ambiguous.append({"name": t["name"], "recipe_ids": t["recipe_ids"],
                                  "codes": hits})
                continue
            code = hits[0]
            hint = rows[code]["category_hint"]
            current_cats = t["categories"]
            entry = {"name": t["name"], "code": code, "recipe_ids": t["recipe_ids"],
                     "hint": hint, "current_categories": sorted(current_cats)}
            confirmed.append(entry)
            # 분류 충돌: 값이 있는데 hint 와 다르면 보고만(덮지 않음). NULL 은 채움 후보.
            if current_cats and hint and all(c != hint for c in current_cats):
                category_conflict.append(entry)
            continue

        # 미매칭: 유사 후보
        close = _close_matches(key, pool_product)
        unmatched.append({"name": t["name"], "recipe_ids": t["recipe_ids"],
                          "close": [names.get(c, c) for c in close]})

    return {"confirmed": confirmed, "category_conflict": category_conflict,
            "ambiguous": ambiguous, "unmatched": unmatched}


# ---------- 보고서 출력 ----------


def print_report(mat: dict, rec: dict) -> None:
    """매칭 보고서 출력(읽기 전용)."""
    print("=" * 70)
    print("자재 매칭")
    print("=" * 70)
    print(f"\n[자재 확정(원자재 마스터): {len(mat['confirmed'])}건]")
    for it in mat["confirmed"]:
        print(f"  id={it['material_id']}  {it['name']}  →  {it['code']}")

    print(f"\n[자재-반제품코드 확정: {len(mat['confirmed_cross'])}건]")
    for it in mat["confirmed_cross"]:
        print(f"  id={it['material_id']}  {it['name']}  →  {it['code']}  (반제품 코드 매칭)")

    print(f"\n[자재 모호: {len(mat['ambiguous'])}건]")
    for it in mat["ambiguous"]:
        tag = "반제품" if it["matched_kind"] == "product" else "원자재"
        print(f"  id={it['material_id']}  {it['name']}  →  후보 {len(it['codes'])}건"
              f"({tag}): {', '.join(it['codes'])}")

    print(f"\n[자재 미매칭: {len(mat['unmatched'])}건]")
    for it in mat["unmatched"]:
        close = f"  유사: {', '.join(it['close'])}" if it["close"] else "  (유사 후보 없음)"
        print(f"  id={it['material_id']}  {it['name']}{close}")

    print("\n" + "=" * 70)
    print("레시피 매칭")
    print("=" * 70)
    print(f"\n[레시피 확정: {len(rec['confirmed'])}건]")
    for it in rec["confirmed"]:
        cats = "/".join(it["current_categories"]) if it["current_categories"] else "NULL"
        cat_note = f"  분류채움→{it['hint']}" if it["hint"] and not it["current_categories"] else ""
        print(f"  {it['name']}  →  {it['code']}  (행 {len(it['recipe_ids'])}개, "
              f"현재분류={cats}{cat_note})")

    print(f"\n[분류 충돌: {len(rec['category_conflict'])}건]")
    for it in rec["category_conflict"]:
        cats = "/".join(it["current_categories"])
        print(f"  {it['name']}  →  {it['code']}  기존={cats} vs hint={it['hint']}  "
              f"(덮지 않음 — 보고만)")

    print(f"\n[레시피 모호: {len(rec['ambiguous'])}건]")
    for it in rec["ambiguous"]:
        print(f"  {it['name']}  →  후보 {len(it['codes'])}건: {', '.join(it['codes'])}"
              f"  (행 {len(it['recipe_ids'])}개)")

    print(f"\n[레시피 미매칭: {len(rec['unmatched'])}건]")
    for it in rec["unmatched"]:
        close = f"  유사: {', '.join(it['close'])}" if it["close"] else "  (유사 후보 없음)"
        print(f"  {it['name']}{close}")

    print("\n" + "=" * 70)
    print("총계")
    print("=" * 70)
    print(f"자재 확정={len(mat['confirmed'])}  반제품코드 확정={len(mat['confirmed_cross'])}"
          f"  자재 모호={len(mat['ambiguous'])}  자재 미매칭={len(mat['unmatched'])}")
    print(f"레시피 확정={len(rec['confirmed'])}  분류 충돌={len(rec['category_conflict'])}"
          f"  레시피 모호={len(rec['ambiguous'])}  레시피 미매칭={len(rec['unmatched'])}")


# ---------- --apply 반영 ----------


def apply_confirmed(conn: sqlite3.Connection, mat: dict, rec: dict) -> dict:
    """확정 후보만 DB 반영. 자재는 code 업데이트, 레시피는 같은 product_name 의 모든
    completed 행에 product_code + (NULL 일 때만) category 부여. 충돌 category 는 건드리지 않음.

    자재 code 는 UNIQUE(partial, WHERE code IS NOT NULL). 같은 ERP 코드에 2개 이상 자재가
    매칭되면(예: GLYCEROL/Glycerol 동일 품목 중복 등록) 첫 자재에만 부여하고 나머지는
    '코드 중복' 충돌로 skip 한다(운영 데이터 정리 대상 — apply 실패로 멈추지 않음).

    반환: {materials_code_set, materials_code_skipped_dup, recipes_code_set,
           recipes_category_filled, code_conflicts}.
    """
    n_mat = 0
    code_conflicts = []  # 같은 code 로 이미 다른 자재에 부여된 충돌
    used_codes: set[str] = set()
    for it in mat["confirmed"] + mat["confirmed_cross"]:
        if it["code"] in used_codes:
            code_conflicts.append({"name": it["name"], "code": it["code"]})
            continue
        conn.execute("UPDATE materials SET code=? WHERE id=?",
                     (it["code"], it["material_id"]))
        used_codes.add(it["code"])
        n_mat += 1

    n_rec = 0
    n_cat = 0
    for it in rec["confirmed"]:
        # 같은 product_name 의 모든 completed 행에 product_code 부여(개정 체인 전체).
        # 대상은 product_code IS NULL 인 행만(재실행 멱등 — 이미 부여된 건 건드리지 않음).
        rows = conn.execute(
            "SELECT id, category FROM recipes WHERE product_name=? AND status='completed' "
            "AND product_code IS NULL",
            (it["name"],),
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE recipes SET product_code=? WHERE id=?",
                         (it["code"], r["id"]))
            n_rec += 1
            # category 는 NULL 일 때만 hint 로 채움(충돌/기존값 건드리지 않음).
            if r["category"] is None and it["hint"]:
                conn.execute("UPDATE recipes SET category=? WHERE id=?",
                             (it["hint"], r["id"]))
                n_cat += 1
    conn.commit()
    return {"materials_code_set": n_mat,
            "materials_code_skipped_dup": len(code_conflicts),
            "recipes_code_set": n_rec,
            "recipes_category_filled": n_cat,
            "code_conflicts": code_conflicts}


# ---------- main ----------


def run(db_arg: str | None, apply: bool) -> dict:
    """매칭 실행 본체(테스트·스크립트 공용). 반환: 매칭 결과(+ apply 시 applied)."""
    conn, db_label = _open_target_db(db_arg)
    try:
        master_count = conn.execute("SELECT COUNT(*) c FROM item_code_master").fetchone()["c"]
        if master_count == 0:
            print(f"[skip] item_code_master 가 비어 있습니다({db_label}). "
                  "먼저 import_item_codes.py 를 실행하세요.")
            return {"empty": True}

        index = _build_master_index(conn)
        mat = match_materials(conn, index)
        rec = match_recipes(conn, index)
        print_report(mat, rec)

        result: dict = {"mat": mat, "rec": rec}
        if apply:
            applied = apply_confirmed(conn, mat, rec)
            print("\n" + "=" * 70)
            print(f"--apply 반영 완료({db_label})")
            print("=" * 70)
            print(f"자재 code 부여: {applied['materials_code_set']}건")
            if applied["materials_code_skipped_dup"]:
                print(f"자재 코드 중복 skip: {applied['materials_code_skipped_dup']}건"
                      f"  (UNIQUE 위반 — 운영 데이터 정리 대상)")
                for cf in applied["code_conflicts"]:
                    print(f"    {cf['name']}  →  {cf['code']}")
            print(f"레시피 product_code 부여: {applied['recipes_code_set']}건"
                  f"  (분류 채움: {applied['recipes_category_filled']}건)")
            result["applied"] = applied
        return result
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ERP 품목코드 자동 매칭(보고서 우선)")
    ap.add_argument("--db", default=None,
                    help="대상 DB 경로(기본: IRMS_DATA_DIR 의 개발 DB)")
    ap.add_argument("--apply", action="store_true",
                    help="확정 후보만 DB 반영(기본은 보고서만)")
    args = ap.parse_args()
    run(args.db, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
