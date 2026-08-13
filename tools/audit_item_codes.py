"""읽기 전용 품목코드 정합성 감사 도구.

materials.code(자재 품목코드) / item_code_master(ERP 품목 마스터) /
일일 ERP 재고 엑셀 / blend_details.material_code(배합 기록 스냅샷) 사이에
끼어든 드리프트를 대조해 보고한다. 품목코드를 둘러싼 값이 어느 한쪽으로만
바뀌거나 잘못 들어가면 재고 검증(LOT) 사각지대가 생기는데, 그 어긋남을
한 화면에 모아 보여주는 도구가 지금까지 없었다.

이 도구는 보고 전용이다. DB 는 sqlite3 읽기 전용(file: URI + mode=ro)으로
열어서, 이 도구가 DB 를 건드릴 방법 자체를 없앤다. UPDATE/INSERT/DELETE/DDL
어느 것도 하지 않는다. 종료 코드는 항상 0(문제가 있어도 보고만 한다).

출력 문자는 모두 CP949 로 인코딩 가능해야 한다(운영 PC 콘솔 코드페이지 949).
한글과 ASCII 만 쓴다 - em dash / 화살표 / 체크 / 이모지 금지.

사용:
  python tools/audit_item_codes.py
  python tools/audit_item_codes.py --db 경로/irms.db
  python tools/audit_item_codes.py --erp-file C:/ErpExcel/ERP_2026-08-13.xlsx
  python tools/audit_item_codes.py --json

섹션:
  [A] 활성 자재 중 품목코드 미부여(NULL)
  [B] 자재 코드가 품목 마스터에 없음
  [C] 코드 표기 이상(공백 / 소문자 / 빈 문자열)
  [D] 자재 코드와 레시피 제품코드 교차 중복(대소문자 무시)
  [E] 품목 마스터 수동 입력(source=manual) 행
  [F] ERP 재고 엑셀에 없는 자재 코드(--erp-file 지정 시)
  [G] 배합 기록(blend_details) 코드 드리프트
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

# erp_lot_service(ERP 엑셀 파서) 를 import 하기 위해 저장소 루트를 path 에 넣는다.
# tools/ 스크립트 관례(unify_material_names.py, import_item_codes.py 참고).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.queries import normalize_token  # noqa: E402  (sys.path 조정 뒤 import)
from src.services import erp_lot_service  # noqa: E402  (sys.path 조정 뒤 import)
# 원자재 코드 계열 상수(erp_lot_service.RAW_MATERIAL_CODE_PREFIXES)도 같은 모듈에서
# 가져온다 — 마스터 임포트(import_item_codes)가 re-export 하지만, 감사 도구는 원 소스를
# 직접 쓴다(단일 진실 원천).


# ── 연결 ────────────────────────────────────────────────────────────────────

def _open_ro(db_path: str) -> sqlite3.Connection:
    """DB 를 읽기 전용으로 연다. mode=ro 로 열면 우발적 쓰기도 sqlite 가 거부한다.

    file: URI 형식은 tools/blend_query.py 의 검증된 패턴을 그대로 따른다.
    as_posix() 로 백슬래시를 슬래시로 바꿔 file URI 에 넣는다.
    """
    path = Path(db_path)
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ── 섹션 ────────────────────────────────────────────────────────────────────

def section_a(conn: sqlite3.Connection) -> dict:
    """[A] 활성(is_active=1) 자재 중 code 미부여(NULL). ERP LOT 검증 사각지대."""
    title = "[A] 활성 자재 중 품목코드 미부여(NULL) - ERP LOT 검증 사각지대"
    if not _table_exists(conn, "materials"):
        return {"key": "A", "title": title, "status": "ok", "items": []}
    rows = conn.execute(
        "SELECT id, name, unit, category FROM materials "
        "WHERE is_active = 1 AND code IS NULL ORDER BY name"
    ).fetchall()
    items = [
        {"id": int(r["id"]), "name": r["name"],
         "unit": r["unit"], "category": r["category"]}
        for r in rows
    ]
    return {"key": "A", "title": title,
            "status": "issues" if items else "ok", "items": items}


def section_b(conn: sqlite3.Connection) -> dict:
    """[B] materials.code 가 item_code_master 에 없는 자재.

    마스터 테이블이 비어 있으면 검사를 생략한다(마스터 미적재). 대소문자만
    다른 코드는 표기 문제([C])이지 '없는 코드'가 아니므로, 여기서는
    대소문자 무시 비교로 찾는다.
    """
    title = "[B] 자재 코드가 품목 마스터에 없음"
    if not _table_exists(conn, "item_code_master"):
        return {"key": "B", "title": title, "status": "skip",
                "note": "마스터 미적재 - 검사 생략", "items": []}
    count = conn.execute("SELECT COUNT(*) AS c FROM item_code_master").fetchone()["c"]
    if count == 0:
        return {"key": "B", "title": title, "status": "skip",
                "note": "마스터 미적재 - 검사 생략", "items": []}
    rows = conn.execute(
        """
        SELECT m.id, m.name, m.code FROM materials m
        WHERE m.code IS NOT NULL AND TRIM(m.code) != ''
          AND NOT EXISTS (
              SELECT 1 FROM item_code_master ic
              WHERE UPPER(ic.code) = UPPER(m.code)
          )
        ORDER BY m.name
        """
    ).fetchall()
    items = [
        {"id": int(r["id"]), "name": r["name"], "code": r["code"]}
        for r in rows
    ]
    # 폐기(retired) 마스터 코드를 쥔 자재 — 코드는 마스터에 '있지만' 현행이 아니다.
    # status 컬럼이 없는 구버전 DB 는 검사 생략(빈 목록).
    try:
        retired_rows = conn.execute(
            """
            SELECT m.id, m.name, m.code, ic.retired_at FROM materials m
            JOIN item_code_master ic ON UPPER(ic.code) = UPPER(m.code)
            WHERE m.code IS NOT NULL AND TRIM(m.code) != ''
              AND COALESCE(ic.status, 'active') = 'retired'
            ORDER BY m.name
            """
        ).fetchall()
        retired_in_use = [
            {"id": int(r["id"]), "name": r["name"], "code": r["code"],
             "retired_at": r["retired_at"]}
            for r in retired_rows
        ]
    except sqlite3.OperationalError:
        retired_in_use = []
    status = "issues" if (items or retired_in_use) else "ok"
    return {"key": "B", "title": title, "status": status,
            "items": items, "retired_in_use": retired_in_use}


def section_c(conn: sqlite3.Connection) -> dict:
    """[C] 코드 표기 이상 - code 가 strip().upper() 결과와 다르거나 빈 문자열."""
    title = "[C] 코드 표기 이상(공백 / 소문자 / 빈 문자열)"
    if not _table_exists(conn, "materials"):
        return {"key": "C", "title": title, "status": "ok", "items": []}
    rows = conn.execute(
        "SELECT id, name, code FROM materials "
        "WHERE code IS NOT NULL ORDER BY name"
    ).fetchall()
    items = []
    for r in rows:
        code = r["code"]
        problems = []
        if code == "":
            problems.append("빈 문자열")
        else:
            normalized = code.strip().upper()
            if code != normalized:
                problems.append("strip().upper() 결과와 다름")
        if problems:
            items.append({
                "id": int(r["id"]), "name": r["name"], "code": code,
                "normalized": code.strip().upper() if code != "" else "",
                "problem": ", ".join(problems),
            })
    return {"key": "C", "title": title,
            "status": "issues" if items else "ok", "items": items}


def section_d(conn: sqlite3.Connection) -> dict:
    """[D] materials.code 와 recipes.product_code 교차 중복(대소문자 무시).

    서로 **다른 품목**이 자재 코드와 제품 코드로 같은 코드를 나눠 쥐면 품목코드
    해석이 꼬인다(자재인지 제품인지). 다만 이름이 같으면 정상이다 - 1차 반제품이
    2차 배합에서 자재로도 등록되는 중간체는 한 품목의 두 얼굴이라 같은 코드를
    일부러 공유한다(임포트가 1차 코드를 자재 행에 승계). 이 정상 공유는 문제
    목록이 아니라 참고(shares)로만 보고한다 - API 의 교차 중복 차단 규칙
    (item_code_routes._product_code_holder)과 같은 판정이다.
    """
    title = "[D] 자재 코드와 레시피 제품코드 교차 중복(대소문자 무시)"
    if not _table_exists(conn, "recipes"):
        return {"key": "D", "title": title, "status": "ok",
                "items": [], "shares": []}
    materials = conn.execute(
        "SELECT id, name, code FROM materials "
        "WHERE code IS NOT NULL AND TRIM(code) != ''"
    ).fetchall()
    recipes = conn.execute(
        "SELECT id, product_name, product_code FROM recipes "
        "WHERE product_code IS NOT NULL AND TRIM(product_code) != ''"
    ).fetchall()
    mat_by_key: dict[str, list] = {}
    for r in materials:
        key = (r["code"] or "").strip().upper()
        if key:
            mat_by_key.setdefault(key, []).append(
                {"id": int(r["id"]), "name": r["name"], "code": r["code"]})
    rec_by_key: dict[str, list] = {}
    for r in recipes:
        key = (r["product_code"] or "").strip().upper()
        if key:
            rec_by_key.setdefault(key, []).append(
                {"id": int(r["id"]), "name": r["product_name"],
                 "code": r["product_code"]})
    dup_keys = sorted(set(mat_by_key) & set(rec_by_key))
    items = []
    shares = []
    for k in dup_keys:
        mat_names = {normalize_token(m["name"] or "") for m in mat_by_key[k]}
        rec_names = {normalize_token(r["name"] or "") for r in rec_by_key[k]}
        entry = {"code_key": k, "materials": mat_by_key[k],
                 "recipes": rec_by_key[k]}
        # 양쪽 이름이 전부 같은 품목이면 중간체의 정상 공유.
        if mat_names and rec_names and mat_names == rec_names:
            shares.append(entry)
        else:
            items.append(entry)
    return {"key": "D", "title": title,
            "status": "issues" if items else "ok",
            "items": items, "shares": shares}


def section_e(conn: sqlite3.Connection) -> dict:
    """[E] item_code_master 의 source='manual' 행. 화면 수동 입력분 점검용."""
    title = "[E] 품목 마스터 수동 입력(source=manual) 행"
    if not _table_exists(conn, "item_code_master"):
        return {"key": "E", "title": title, "status": "ok", "items": []}
    rows = conn.execute(
        """
        SELECT code, name, spec, unit, kind, category_hint, source, imported_at
        FROM item_code_master WHERE source = 'manual' ORDER BY code
        """
    ).fetchall()
    items = [dict(r) for r in rows]
    return {"key": "E", "title": title,
            "status": "issues" if items else "ok", "items": items}


def section_f(conn: sqlite3.Connection, erp_file: str | None) -> dict:
    """[F] 원자재 코드 중 ERP 재고 엑셀에 등장하지 않는 것.

    ERP 일일 재고 엑셀은 **원재료 전용**이다. 반제품 코드(B 계열 등)나 ERP 에
    등록되지 않은 소모성 품목의 관리용 코드는 이 파일에 없는 것이 정상이므로,
    대조는 원자재 코드 계열(erp_lot_service.RAW_MATERIAL_CODE_PREFIXES = AS/AC/AH/AW
    — 마스터 임포트와 같은 기준)만 대상으로 한다. 제외된 코드는 개수로만 알린다.

    엑셀 파싱은 src/services/erp_lot_service.py 의 기존 파서(_parse_file)를
    그대로 가져와 쓴다 - 열 인덱스를 다시 하드코딩하지 않는다. _parse_file 은
    임의 경로 한 파일을 파싱하는 유일한 진입점이다(load_lots 는 고정 디렉터리의
    최신 파일만 본다). 미지정 / 없음 / 열기 실패면 검사를 생략한다.
    """
    title = "[F] ERP 재고 엑셀에 없는 원자재 코드"
    if not erp_file:
        return {"key": "F", "title": title, "status": "skip",
                "note": "검사 생략 (ERP 파일 미지정)", "items": []}
    if not os.path.exists(erp_file):
        return {"key": "F", "title": title, "status": "skip",
                "note": "검사 생략 (ERP 파일 없음)", "items": []}
    try:
        mtime = os.path.getmtime(erp_file)
        parsed = erp_lot_service._parse_file(erp_file, mtime)
    except Exception:
        return {"key": "F", "title": title, "status": "skip",
                "note": "검사 생략 (ERP 파일 열기 실패)", "items": []}
    erp_codes_upper = {
        (c or "").strip().upper() for c in parsed["lots"].keys()
    }
    mat_rows = conn.execute(
        "SELECT id, name, code FROM materials "
        "WHERE code IS NOT NULL AND TRIM(code) != '' ORDER BY name"
    ).fetchall()
    items = []
    skipped_non_raw = 0
    for r in mat_rows:
        code_key = (r["code"] or "").strip().upper()
        if not code_key.startswith(erp_lot_service.RAW_MATERIAL_CODE_PREFIXES):
            skipped_non_raw += 1   # 반제품/관리용 코드 - 원자재 재고 파일 대상 아님
            continue
        if code_key not in erp_codes_upper:
            items.append(
                {"id": int(r["id"]), "name": r["name"], "code": r["code"]})
    return {
        "key": "F", "title": title,
        "status": "issues" if items else "ok", "items": items,
        "skipped_non_raw": skipped_non_raw,
        "file_name": parsed["file_name"],
        "file_date": parsed["file_date"],
    }


def section_g(conn: sqlite3.Connection) -> dict:
    """[G] blend_details 코드 드리프트.

    (1) material_id 연결 행 중 저장 material_code 가 현재 materials.code 와 다른
        것을 코드쌍(저장 / 현재)별로 집계. 빈 문자열과 NULL 은 같은 '코드 없음'
        으로 취급하고 대소문자 차이는 무시한다 - 표기 문제는 [C] 의 몫이고,
        여기 남는 것은 실제로 다른 코드가 박힌 행이다.
    (2) 저장 material_code 가 materials.code 에도 item_code_master 에도 없는
        코드(어디에도 속하지 않는 유령 코드) 목록 - 행 수 상위 20.
    """
    title = "[G] 배합 기록(blend_details) 코드 드리프트"
    if not _table_exists(conn, "blend_details"):
        return {"key": "G", "title": title, "status": "ok", "g1": [], "g2": []}
    g1_rows = conn.execute(
        """
        SELECT bd.material_id AS material_id,
               bd.material_code AS stored_code,
               m.code AS current_code,
               m.name AS material_name,
               COUNT(*) AS row_count
        FROM blend_details bd
        JOIN materials m ON m.id = bd.material_id
        WHERE bd.material_id IS NOT NULL
        GROUP BY bd.material_id, bd.material_code, m.code, m.name
        HAVING UPPER(COALESCE(bd.material_code, '')) != UPPER(COALESCE(m.code, ''))
        ORDER BY m.name, bd.material_code
        """
    ).fetchall()
    g1 = [
        {"material_id": int(r["material_id"]),
         "material_name": r["material_name"],
         "stored_code": r["stored_code"],
         "current_code": r["current_code"],
         "row_count": int(r["row_count"])}
        for r in g1_rows
    ]

    has_master = _table_exists(conn, "item_code_master")
    master_clause = ""
    if has_master:
        master_clause = (
            " AND UPPER(bd.material_code) NOT IN "
            "(SELECT UPPER(code) FROM item_code_master) "
        )
    g2_rows = conn.execute(
        f"""
        SELECT UPPER(bd.material_code) AS code,
               MAX(bd.material_code) AS stored_code_sample,
               MAX(bd.material_name) AS sample_name,
               COUNT(*) AS row_count
        FROM blend_details bd
        WHERE bd.material_code IS NOT NULL AND TRIM(bd.material_code) != ''
          AND UPPER(bd.material_code) NOT IN (
              SELECT UPPER(code) FROM materials
              WHERE code IS NOT NULL AND TRIM(code) != ''
          )
          {master_clause}
        GROUP BY UPPER(bd.material_code)
        ORDER BY row_count DESC, code
        LIMIT 20
        """
    ).fetchall()
    g2 = [
        {"code": r["code"],
         "stored_code_sample": r["stored_code_sample"],
         "sample_name": r["sample_name"],
         "row_count": int(r["row_count"])}
        for r in g2_rows
    ]
    status = "issues" if (g1 or g2) else "ok"
    return {"key": "G", "title": title, "status": status, "g1": g1, "g2": g2}


# ── 보고 ────────────────────────────────────────────────────────────────────

def run_audit(conn: sqlite3.Connection, erp_file: str | None) -> dict:
    return {
        "A": section_a(conn),
        "B": section_b(conn),
        "C": section_c(conn),
        "D": section_d(conn),
        "E": section_e(conn),
        "F": section_f(conn, erp_file),
        "G": section_g(conn),
    }


def _print_items(key: str, items: list) -> None:
    if key == "A":
        for it in items:
            cat = it.get("category") or "-"
            print(f"  id={it['id']} | {it['name']} | 단위={it.get('unit') or '-'} "
                  f"| 분류={cat}")
    elif key == "B":
        for it in items:
            print(f"  {it['code']} | {it['name']} (id={it['id']})")
    elif key == "C":
        for it in items:
            print(f"  {it['code']!r} -> {it['normalized']!r} | {it['name']} "
                  f"(id={it['id']}) | {it['problem']}")
    elif key == "D":
        for it in items:
            mats = ", ".join(f"{m['code']}({m['name']})" for m in it["materials"])
            recs = ", ".join(f"{r['code']}({r['name']})" for r in it["recipes"])
            print(f"  코드 {it['code_key']}:")
            print(f"    자재: {mats}")
            print(f"    레시피: {recs}")
    elif key == "E":
        for it in items:
            spec = it.get("spec") or "-"
            print(f"  {it['code']} | {it['name']} | 규격={spec} "
                  f"| 종류={it.get('kind') or '-'} "
                  f"| 분류={it.get('category_hint') or '-'} "
                  f"| {it.get('imported_at') or '-'}")
    elif key == "F":
        for it in items:
            print(f"  {it['code']} | {it['name']} (id={it['id']})")


def _print_b(sec: dict) -> None:
    if sec["status"] == "skip":
        print(f"  {sec.get('note', '검사 생략')}")
        return
    items = sec.get("items", [])
    retired = sec.get("retired_in_use", [])
    if not items and not retired:
        print("  이상 없음")
        return
    for it in items:
        print(f"  {it['code']} | {it['name']} (id={it['id']})")
    if retired:
        print("  폐기 코드 사용 중 (마스터에는 있으나 현행 아님):")
        for it in retired:
            when = f" (폐기 {it['retired_at']})" if it.get("retired_at") else ""
            print(f"    {it['code']} | {it['name']} (id={it['id']}){when}")


def _print_d(sec: dict) -> None:
    items = sec.get("items", [])
    shares = sec.get("shares", [])
    if not items:
        print("  이상 없음")
    else:
        _print_items("D", items)
    if shares:
        names = ", ".join(
            f"{s['code_key']}({s['materials'][0]['name']})" for s in shares
        )
        print(f"  참고: 같은 품목의 정상 공유 {len(shares)}건 "
              f"(1차 반제품이 2차 자재로도 등록된 중간체) - {names}")


def _print_g(sec: dict) -> None:
    g1 = sec.get("g1", [])
    g2 = sec.get("g2", [])
    if not g1 and not g2:
        print("  이상 없음")
        return
    print("  (1) 저장 코드가 현재 자재 코드와 다름 (material_id 연결 행):")
    if g1:
        for it in g1:
            cur = it["current_code"] if it["current_code"] is not None else "(NULL)"
            stored = it["stored_code"] if it["stored_code"] is not None else "(NULL)"
            print(f"    {it['material_name']} (id={it['material_id']}): "
                  f"저장={stored} / 현재={cur} | {it['row_count']}행")
    else:
        print("    이상 없음")
    print("  (2) 저장 코드가 자재 / 마스터 어디에도 없음 (행 수 상위 20):")
    if g2:
        for it in g2:
            print(f"    {it['code']} | {it.get('sample_name') or '-'} "
                  f"| {it['row_count']}행")
    else:
        print("    이상 없음")


def print_human(results: dict, db_label: str) -> None:
    print(f"품목코드 정합성 감사 | DB: {db_label}")
    for key in ("A", "B", "C", "D", "E", "F", "G"):
        sec = results[key]
        print()
        print(sec["title"])
        if key == "F" and sec.get("file_name"):
            date_part = f" ({sec['file_date']})" if sec.get("file_date") else ""
            print(f"  ERP 파일: {sec['file_name']}{date_part}")
            if sec.get("skipped_non_raw"):
                print(f"  참고: 반제품/관리용 코드 {sec['skipped_non_raw']}건은 "
                      f"원자재 재고 파일 대상이 아니라 대조에서 제외")
        if key == "B":
            _print_b(sec)
            continue
        if key == "D":
            _print_d(sec)
            continue
        if key == "G":
            _print_g(sec)
            continue
        if sec["status"] == "skip":
            print(f"  {sec.get('note', '검사 생략')}")
            continue
        items = sec.get("items", [])
        if not items:
            print("  이상 없음")
            continue
        _print_items(key, items)


def print_json(results: dict, db_label: str) -> None:
    payload = {"db": db_label, "sections": results}
    print(json.dumps(payload, ensure_ascii=False))


# ── 진입 ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="품목코드 정합성 감사(읽기 전용). DB 에 쓰지 않는다."
    )
    ap.add_argument("--db", default="data/irms.db",
                    help="감사 대상 DB 경로(기본 data/irms.db)")
    ap.add_argument("--erp-file", default=None,
                    help="ERP 재고 엑셀(ERP_*.xlsx) 경로")
    ap.add_argument("--json", action="store_true",
                    help="사람용 보고 대신 JSON 으로 출력")
    args = ap.parse_args(argv)

    db_label = args.db
    if not os.path.exists(args.db):
        # 보고 전용 도구 - 종료 코드는 항상 0.
        print(f"DB 없음: {args.db}", file=sys.stderr)
        return 0

    conn = _open_ro(args.db)
    try:
        results = run_audit(conn, args.erp_file)
    finally:
        conn.close()

    if args.json:
        print_json(results, db_label)
    else:
        print_human(results, db_label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
