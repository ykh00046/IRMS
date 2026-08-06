"""구 배합프로그램_v1.2 실적서 → BRM 배합 기록 이관 (제품 LOT 누락분만).

전제(사용자 규칙 2026-08-06):
- 대조 기준은 **제품 LOT 존재 여부뿐**이다. 구 툴은 수기 입력이라 같은 LOT 의
  배합량 숫자가 BRM 과 달라도 같은 기록으로 보고 건드리지 않는다.
- 이미 BRM 에 있는 LOT 은 무조건 건너뛴다(실행 시점의 DB 를 다시 확인하므로
  낡은 백업으로 비교했어도 안전).

동작:
- 실적서 excel 폴더의 각 <제품LOT>.xlsx 를 파싱해 blend_records + blend_details
  로 삽입한다. 재고·LOT 소비 등 부수효과는 일으키지 않는다(과거 이력 백필).
- created_by 마커('구프로그램 이관')로 이관분을 식별할 수 있다(정리 한정 가능,
  바인더 점도 이관과 동일 패턴).
- 레시피 연결: 제품명(LOT 에서 날짜부 8자리 제거)이 BRM 레시피와 일치하면
  recipe_id 연결, 없으면 NULL(과거 이력 보존 — 바인더 이관과 동일 방침).

사용(운영 PC, 서버 정지 필요 없음 — 짧은 트랜잭션):
  # 미리보기(아무것도 안 씀):
  python tools/import_legacy_records.py --source "배합프로그램_v1.2/배합프로그램_v1.2/실적서/excel"
  # 실제 삽입:
  python tools/import_legacy_records.py --source ... --apply
IRMS_DATA_DIR 환경변수(또는 --data-dir)가 운영 데이터 폴더를 가리켜야 한다.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

MARKER = "구프로그램 이관"
LOT_DATE_RE = re.compile(r"^(.*?)(\d{8})$")  # 제품명 + YYMMDDNN


def parse_sheet(path: Path) -> dict:
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    # 2행: 날짜/작업자/시간, 3행: 저울 — 라벨 위치가 밀려도 견디게 문자열 스캔.
    meta = {"date": None, "worker": None, "time": None, "scale": None}
    for row in rows[:4]:
        for cell in row:
            if not isinstance(cell, str):
                continue
            if "날짜" in cell and ":" in cell:
                meta["date"] = cell.split(":", 1)[1].strip()
            elif "작업자" in cell:
                meta["worker"] = cell.split(":", 1)[1].strip()
            elif "작업시간" in cell:
                meta["time"] = cell.split(":", 1)[1].strip()
            elif "저울" in cell:
                meta["scale"] = cell.split(":", 1)[1].strip()
    # 헤더 행 찾기('약품번호'로 시작) — 그 다음부터 자재 행.
    header_idx = None
    for i, row in enumerate(rows):
        if row and isinstance(row[0], str) and "약품번호" in row[0]:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("헤더(약품번호) 행을 찾지 못했다")
    details = []
    product_lot = None
    total_x100 = None
    for row in rows[header_idx + 1:]:
        if not row or all(v is None for v in row):
            continue
        cells = list(row) + [None] * (7 - len(row))
        # 첫 자재 행: A=제품LOT, B=배합량(100g), C~G=자재. 이후 행: A,B 비고 C~G 자재.
        if cells[0] is not None and product_lot is None:
            product_lot = str(cells[0]).strip()
            total_x100 = cells[1]
        name, lot, ratio, theory, actual = cells[2:7]
        if name is None and lot is None and theory is None:
            continue
        details.append({
            "material_name": str(name).strip() if name is not None else "",
            "material_lot": str(lot).strip() if lot is not None else "",
            "ratio": float(ratio) if ratio is not None else None,
            "theory_amount": float(theory) if theory is not None else 0.0,
            "actual_amount": float(actual if actual is not None else theory or 0.0),
        })
    if not product_lot:
        raise ValueError("제품 LOT 을 찾지 못했다")
    if not details:
        raise ValueError("자재 행이 없다")
    # 총량 = 자재 이론합. 구 툴의 '배합량(100g)' 칸은 제품마다 의미가 달라
    # (6-1 TOP=×100, APB·신규 S2+코팅=다른 단위/자유 표기) 신뢰할 수 없다 —
    # 실측 전수 확인(2026-08-06 dry-run 137건).
    sum_theory = round(sum(d["theory_amount"] for d in details), 2)
    total = sum_theory
    del total_x100  # 참고용으로만 파싱했다
    file_lot = path.stem.strip()
    if file_lot != product_lot:
        # 파일명이 기준(누락 대조가 파일명으로 이뤄졌으므로) — 내부 표기가 다르면 경고만.
        pass
    return {
        "product_lot": file_lot,
        "sheet_lot": product_lot,
        "work_date": meta["date"],
        "work_time": meta["time"],
        "worker": meta["worker"] or "미상",
        "scale": meta["scale"] or "M-65",
        "total_amount": total,
        "sum_theory": sum_theory,
        "details": details,
    }


def product_name_of(lot: str) -> str:
    m = LOT_DATE_RE.match(lot)
    return (m.group(1) if m else lot).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="실적서 excel 폴더")
    ap.add_argument("--data-dir", default=os.environ.get("IRMS_DATA_DIR", "data"))
    ap.add_argument("--apply", action="store_true", help="실제 삽입(기본은 미리보기)")
    args = ap.parse_args()

    src = Path(args.source)
    db_path = Path(args.data_dir) / "irms.db"
    if not db_path.exists():
        print(f"DB 없음: {db_path}", file=sys.stderr)
        return 2
    files = sorted(f for f in src.iterdir() if f.suffix.lower() == ".xlsx")
    if not files:
        print(f"실적서 xlsx 없음: {src}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    existing = {r[0] for r in conn.execute("SELECT product_lot FROM blend_records")}
    recipes = {
        str(r["product_name"]).strip(): int(r["id"])
        for r in conn.execute(
            "SELECT id, product_name FROM recipes ORDER BY id"
        )
    }

    parsed, skipped, inserted, errors = 0, 0, 0, []
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for f in files:
        lot = f.stem.strip()
        if lot in existing:
            skipped += 1
            continue
        try:
            rec = parse_sheet(f)
        except Exception as e:  # noqa: BLE001 — 파일 단위 보고
            errors.append(f"{f.name}: {e}")
            continue
        parsed += 1
        pname = product_name_of(lot)
        rid = recipes.get(pname)
        print(f"{'[삽입]' if args.apply else '[예정]'} {lot} · {rec['work_date']}"
              f" · {rec['worker']} · {rec['total_amount']:g}g"
              f" · 자재 {len(rec['details'])}종 · 레시피 {'연결' if rid else '없음(NULL)'}")
        if not args.apply:
            continue
        cur = conn.execute(
            "INSERT INTO blend_records (product_lot, recipe_id, product_name, ink_name,"
            " worker, work_date, work_time, total_amount, scale, status, note,"
            " created_by, created_at, manual_entry)"
            " VALUES (?,?,?,?,?,?,?,?,?,'completed',?,?,?,0)",
            (
                lot, rid, pname, "none", rec["worker"], rec["work_date"],
                rec["work_time"], rec["total_amount"], rec["scale"],
                None, MARKER, now,
            ),
        )
        rec_id = cur.lastrowid
        for seq, d in enumerate(rec["details"], start=1):
            conn.execute(
                "INSERT INTO blend_details (blend_record_id, material_id, material_code,"
                " material_name, material_lot, ratio, theory_amount, actual_amount,"
                " sequence_order, created_at, manual_entry, carried_over)"
                " VALUES (?,NULL,'',?,?,?,?,?,?,?,0,0)",
                (
                    rec_id, d["material_name"], d["material_lot"], d["ratio"],
                    d["theory_amount"], d["actual_amount"], seq, now,
                ),
            )
        inserted += 1
    if args.apply:
        conn.commit()
    conn.close()

    print(f"\n요약: 대상 {len(files)} · 이미 있음(건너뜀) {skipped}"
          f" · 파싱 {parsed} · 삽입 {inserted if args.apply else 0}"
          f"{' (미리보기 — --apply 로 실제 삽입)' if not args.apply else ''}")
    if errors:
        print("\n파싱 실패:")
        for e in errors:
            print("  !", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
