"""ERP 품목 마스터(code*.xlsx) → item_code_master 임포트 도구.

item-code P1 의 마스터 적재기. materials.code / recipes.product_code 부여는
매칭 단계(P2)에서 별도 스크립트가 담당 — 이 스크립트는 마스터만 채운다.

소스 엑셀(루트, 읽기 전용 — 커밋·이동 금지):
  - code.xlsx  : 전 품목 마스터(품목코드/품목명/규격/기준단위/LOT/품목구분/대분류/중분류)
                 대분류=='원자재' 행만 kind='material' 로 적재(포장재/소모품 등은 skip).
  - code2~4.xlsx : 반제품(품목코드/품명/규격/단위/회계분류/제품구분/...).
                 전 행을 kind='product' 로 적재. category_hint 는 제품구분에서 매핑.

코드 정규화: strip + upper (bc0001 → BC0001). 이름은 strip 만.
빈 코드/빈 이름 행은 skip. 같은 code 재임포트 시 갱신(upsert, imported_at 갱신).

사용:
  python tools/import_item_codes.py --material code.xlsx
  python tools/import_item_codes.py --product code2.xlsx --product code3.xlsx --product code4.xlsx
  python tools/import_item_codes.py --material code.xlsx --product code2.xlsx --dry-run
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_connection, init_db  # noqa: E402  (sys.path 조정 후 import)
from src.db.time_utils import utc_now_text  # noqa: E402


# 제품구분(엑셀 원문) → IRMS 레시피 분류(category_hint) 매핑.
# code2~4 의 '제품구분' 열은 '잉크코드'/'합성코드'/'약품코드' 이며, IRMS 분류는
# '잉크'/'합성'/'약품' 이다(용수는 ERP 에 없음 — IRMS 자체 분류). 그 외 값은 원문 유지.
_PRODUCT_CATEGORY_HINT = {
    "잉크코드": "잉크",
    "합성코드": "합성",
    "약품코드": "약품",
}


def _norm_code(value) -> str | None:
    """품목코드 정규화: strip + upper. 빈 값 → None."""
    if value is None:
        return None
    s = str(value).strip()
    return s.upper() if s else None


def _norm_text(value) -> str | None:
    """일반 텍스트 정규화: strip 만. 빈 값 → None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _read_xlsx(path: str):
    """엑셀 첫 시트를 (1-based row, 1-based col) cell 접근 객체로 반환."""
    import openpyxl

    return openpyxl.load_workbook(path, data_only=True).active


def _upsert_master(conn: sqlite3.Connection, *, code: str, name: str, spec, unit,
                   kind: str, category_hint, source: str, imported_at: str) -> None:
    """item_code_master upsert — 같은 code 면 갱신(imported_at 포함)."""
    conn.execute(
        """
        INSERT INTO item_code_master
            (code, name, spec, unit, kind, category_hint, source, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            spec = excluded.spec,
            unit = excluded.unit,
            kind = excluded.kind,
            category_hint = excluded.category_hint,
            source = excluded.source,
            imported_at = excluded.imported_at
        """,
        (code, name, _norm_text(spec), _norm_text(unit), kind,
         _norm_text(category_hint), source, imported_at),
    )


# 배합 원료 계열 코드 prefix. 운영 스냅샷 리허설(2026-07-16) 결과 실제 배합 자재 20종이
# '소모품' 대분류에 있어(예: AIBN=AC0006, Dibutyltin dilaurate=AS0052) 대분류 필터로는
# 누락된다 — 코드 prefix(AS/AC/AH/AW = 원자재 117 + 소모품 83 = 200행)가 정확한 기준.
# AA(상품)·CB/CL/SP(포장재·소모품 잡류)는 배합과 무관 → 제외.
MATERIAL_CODE_PREFIXES = ("AS", "AC", "AH", "AW")


def import_material_master(
    conn: sqlite3.Connection, path: str, *, source: str = "code",
    dry_run: bool = False,
) -> dict:
    """code.xlsx 형식 → item_code_master (kind='material').

    코드 prefix 가 MATERIAL_CODE_PREFIXES(AS/AC/AH/AW)인 행만 적재(배합 원료 계열).
    category_hint = 대분류/중분류. 반환: {read, imported, skipped, skipped_non_material}.
    """
    ws = _read_xlsx(path)
    now = utc_now_text()
    read = imported = skipped_non_material = skipped_empty = 0

    for r in range(2, ws.max_row + 1):  # 1행은 헤더
        code_raw = ws.cell(r, 1).value
        name_raw = ws.cell(r, 2).value
        spec = ws.cell(r, 3).value
        unit = ws.cell(r, 4).value
        dae = ws.cell(r, 7).value   # 대분류
        joong = ws.cell(r, 8).value  # 중분류
        read += 1

        code = _norm_code(code_raw)
        # 배합 원료 계열 prefix 만. 포장재/상품/기타 소모품 잡류는 배합과 무관 → skip.
        if not code or not code.startswith(MATERIAL_CODE_PREFIXES):
            skipped_non_material += 1
            continue

        name = _norm_text(name_raw)
        if not name:
            skipped_empty += 1
            continue

        # 대분류/중분류를 함께 보존(원자재 vs 소모품 구분이 화면 안내에 유용)
        hint = "/".join(str(x).strip() for x in (dae, joong) if x and str(x).strip())
        if not dry_run:
            _upsert_master(
                conn, code=code, name=name, spec=spec, unit=unit,
                kind="material", category_hint=hint or None, source=source, imported_at=now,
            )
        imported += 1

    if not dry_run:
        conn.commit()
    return {
        "read": read,
        "imported": imported,
        "skipped_non_material": skipped_non_material,
        "skipped_empty": skipped_empty,
    }


def import_product_master(
    conn: sqlite3.Connection, path: str, *, source: str = "code2",
    dry_run: bool = False,
) -> dict:
    """code2~4.xlsx 형식 → item_code_master (kind='product').

    전 행 적재. category_hint = 제품구분에서 매핑(잉크코드→잉크 등, 그 외 원문).
    반환: {read, imported, skipped_empty, category_breakdown}.
    """
    ws = _read_xlsx(path)
    now = utc_now_text()
    read = imported = skipped_empty = 0
    cats: Counter = Counter()

    for r in range(2, ws.max_row + 1):  # 1행은 헤더
        code_raw = ws.cell(r, 1).value
        name_raw = ws.cell(r, 2).value
        spec = ws.cell(r, 3).value
        unit = ws.cell(r, 4).value
        prod_gubun = ws.cell(r, 6).value  # 제품구분
        read += 1

        code = _norm_code(code_raw)
        name = _norm_text(name_raw)
        if not code or not name:
            skipped_empty += 1
            continue

        # 제품구분 → IRMS 분류 매핑. 매핑표에 없으면 원문 유지(추적 가능).
        gubun_norm = _norm_text(prod_gubun) or ""
        category_hint = _PRODUCT_CATEGORY_HINT.get(gubun_norm, gubun_norm or None)
        cats[str(category_hint)] += 1

        if not dry_run:
            _upsert_master(
                conn, code=code, name=name, spec=spec, unit=unit,
                kind="product", category_hint=category_hint,
                source=source, imported_at=now,
            )
        imported += 1

    if not dry_run:
        conn.commit()
    return {
        "read": read,
        "imported": imported,
        "skipped_empty": skipped_empty,
        "category_breakdown": dict(cats),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ERP 품목 마스터 → item_code_master 임포트")
    ap.add_argument("--material", action="append", default=[],
                    help="code.xlsx 형식 경로(원자재만). 복수 지정 가능.")
    ap.add_argument("--product", action="append", default=[],
                    help="code2~4 형식 경로(반제품 전체). 복수 지정 가능.")
    ap.add_argument("--db", default=None,
                    help="대상 DB 경로(기본: IRMS_DATA_DIR 의 개발 DB)")
    ap.add_argument("--dry-run", action="store_true",
                    help="변경 없이 요약만 출력")
    args = ap.parse_args()

    if not (args.material or args.product):
        ap.error("--material 또는 --product 중 하나 이상을 지정하세요.")

    # DB 경로 resolve: 명시 --db > 환경(IRMS_DATA_DIR 관례). import_legacy.py 와 동일.
    if args.db:
        os.environ["IRMS_DATA_DIR"] = str(os.path.dirname(os.path.abspath(args.db)))
        # DATABASE_PATH 는 DATA_DIR/irms.db 규칙을 따르므로 파일명이 irms.db 가 아니면
        # 경로를 그대로 쓸 수 있게 별도 연결. 대부분 관례 경로(--db 미지정)를 쓴다.
        db_file = os.path.abspath(args.db)
        if os.path.basename(db_file) != "irms.db":
            print(f"[db] 명시 경로 사용(관례와 파일명이 다름): {db_file}")

    init_db()  # 스키마 보장(item_code_master 포함)
    if args.db and os.path.basename(os.path.abspath(args.db)) != "irms.db":
        # 비관례 경로: init_db(관례 DB) 로 스키마만 잡고 실제 대상은 별도 연결.
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
    else:
        conn = get_connection()

    try:
        totals: Counter = Counter()
        if args.material:
            for f in args.material:
                r = import_material_master(conn, f, source="code", dry_run=args.dry_run)
                tag = "원자재" if not args.dry_run else "원자재(예정)"
                print(f"[{tag}] {f}: read={r['read']} imported={r['imported']} "
                      f"skipped(비원자재)={r['skipped_non_material']} skipped(빈값)={r['skipped_empty']}")
                totals["material"] += r["imported"]
        if args.product:
            for i, f in enumerate(args.product, start=2):
                src = f"code{i}" if i - 2 < len(args.product) else f"code{i}"
                r = import_product_master(conn, f, source=src, dry_run=args.dry_run)
                tag = "반제품" if not args.dry_run else "반제품(예정)"
                print(f"[{tag}] {f}: read={r['read']} imported={r['imported']} "
                      f"skipped(빈값)={r['skipped_empty']} 분류={r['category_breakdown']}")
                totals["product"] += r["imported"]
        print(f"[총계] material={totals['material']} product={totals['product']}"
              f"{' [DRY-RUN — 변경 없음]' if args.dry_run else ''}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
