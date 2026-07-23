"""구글 시트(구 시스템 백업) ↔ BRM 배합 기록 제품 LOT 전수 대조 도구.

시스템 전환(2026-07) 검증용: 구 시스템의 백업 시트를 기준으로, 시트에 있는
반제품(제품) LOT 가 BRM(blend_records.product_lot)에 빠짐없이 있는지 확인한다.
값 차이(배합량·원재료 LOT 등)는 비교하지 않는다 — 사용자 결정(2026-07-23):
병행 운영 중 작업자가 BRM 기록을 실수로 누락한 LOT 만 잡는다.

사용:
  1) 구글 시트를 파일 > 다운로드 > CSV 또는 Excel(.xlsx) 로 내려받는다.
     (헤더: 제품LOT | 작업자 | 레시피 | 배합량(g) | 작업일시 | ... — 자재별 1행)
  2) python tools/check_sheet_lots.py --sheet 내려받은파일.csv --db 운영백업.db
     · --db 는 운영 DB 사본(backups/irms_*.db)을 권장 — 이 도구는 읽기 전용.
     · --since 2026-07-01 처럼 주면 그 날짜(작업일시) 이후 LOT 만 대조.
  3) 리포트가 콘솔 + (--out 지정 시) 파일로 남는다. "누락 0건"이면 전환 안전.

판정:
  - 누락      : 시트에 있는데 BRM 에 없음 → 반드시 확인(작업자 기입 누락 후보)
  - 취소됨    : BRM 에 있으나 status='canceled' → 의도된 취소인지 확인
  - 정상      : BRM 에 존재(취소 아님)
LOT 비교는 strip + 대문자 정규화(공백/대소문자 차이는 동일 취급).
"""

import argparse
import csv
import io
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path


def _norm(lot: str) -> str:
    return (lot or "").strip().upper()


def load_sheet_lots(path: Path) -> "OrderedDict[str, dict]":
    """시트 파일(csv/xlsx)에서 제품LOT → {first_date, rows} 수집(등장 순서 유지)."""
    lots: "OrderedDict[str, dict]" = OrderedDict()

    def feed(lot_raw, date_raw):
        lot = _norm(str(lot_raw))
        if not lot or lot == "제품LOT":  # 헤더/빈 행
            return
        entry = lots.setdefault(lot, {"first_date": str(date_raw or "").strip(), "rows": 0})
        entry["rows"] += 1
        if not entry["first_date"]:
            entry["first_date"] = str(date_raw or "").strip()

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl

        ws = openpyxl.load_workbook(path, data_only=True, read_only=True).active
        header_seen = False
        lot_idx, date_idx = 0, 4  # 기본: 제품LOT=1열, 작업일시=5열
        for row in ws.iter_rows(values_only=True):
            if not row or all(v in (None, "") for v in row):
                continue
            if not header_seen:
                header_seen = True
                cells = [str(v).strip() if v is not None else "" for v in row]
                if "제품LOT" in cells:
                    lot_idx = cells.index("제품LOT")
                    if "작업일시" in cells:
                        date_idx = cells.index("작업일시")
                    continue  # 헤더 행 소비
                # 헤더가 없으면 첫 행부터 데이터로 취급(기본 인덱스)
            feed(row[lot_idx] if lot_idx < len(row) else "",
                 row[date_idx] if date_idx < len(row) else "")
    else:
        # CSV — 구글 시트 내려받기 기본은 UTF-8. BOM 허용.
        with io.open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header_seen = False
            lot_idx, date_idx = 0, 4
            for row in reader:
                if not row or all(not (c or "").strip() for c in row):
                    continue
                if not header_seen:
                    header_seen = True
                    cells = [c.strip() for c in row]
                    if "제품LOT" in cells:
                        lot_idx = cells.index("제품LOT")
                        if "작업일시" in cells:
                            date_idx = cells.index("작업일시")
                        continue
                feed(row[lot_idx] if lot_idx < len(row) else "",
                     row[date_idx] if date_idx < len(row) else "")
    return lots


def load_brm_lots(db_path: Path) -> dict:
    """BRM 기록의 제품 LOT → status 맵(읽기 전용 접속)."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT product_lot, status FROM blend_records WHERE product_lot IS NOT NULL"
    ).fetchall()
    conn.close()
    result: dict = {}
    for r in rows:
        lot = _norm(r["product_lot"])
        if not lot:
            continue
        # 같은 LOT 이 여러 행이면 '취소 아님' 이 하나라도 있으면 정상으로 취급.
        prev = result.get(lot)
        status = (r["status"] or "").strip()
        if prev is None or (prev == "canceled" and status != "canceled"):
            result[lot] = status
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="시트↔BRM 제품 LOT 전수 대조")
    parser.add_argument("--sheet", required=True, help="시트 내려받기 파일(.csv/.xlsx)")
    parser.add_argument("--db", required=True, help="BRM DB(운영 백업 사본 권장)")
    parser.add_argument("--since", default=None, help="이 날짜(YYYY-MM-DD) 이후 작업일시만 대조")
    parser.add_argument("--out", default=None, help="리포트 저장 파일(UTF-8)")
    args = parser.parse_args()

    sheet_lots = load_sheet_lots(Path(args.sheet))
    brm = load_brm_lots(Path(args.db))

    if args.since:
        sheet_lots = OrderedDict(
            (lot, info) for lot, info in sheet_lots.items()
            if (info["first_date"] or "")[:10] >= args.since
        )

    missing, canceled, ok = [], [], 0
    for lot, info in sheet_lots.items():
        status = brm.get(lot)
        if status is None:
            missing.append((lot, info["first_date"]))
        elif status == "canceled":
            canceled.append((lot, info["first_date"]))
        else:
            ok += 1

    lines = []
    lines.append("=== 시트 ↔ BRM 제품 LOT 대조 리포트 ===")
    lines.append(f"시트 LOT: {len(sheet_lots)}건"
                 + (f" (작업일시 {args.since} 이후)" if args.since else ""))
    lines.append(f"정상(BRM 존재): {ok}건 · 취소됨: {len(canceled)}건 · 누락: {len(missing)}건")
    if missing:
        lines.append("")
        lines.append("--- 누락 (시트에 있으나 BRM 에 없음 — 확인 필수) ---")
        for lot, date in missing:
            lines.append(f"  {lot}  (작업일시 {date or '-'})")
    if canceled:
        lines.append("")
        lines.append("--- 취소됨 (BRM 에서 취소 처리 — 의도 확인) ---")
        for lot, date in canceled:
            lines.append(f"  {lot}  (작업일시 {date or '-'})")
    if not missing and not canceled:
        lines.append("")
        lines.append(">>> 누락 0건 — 전환 안전 기준 충족.")

    report = "\n".join(lines)
    # Windows cp949 콘솔 대비 — UTF-8 강제(기존 도구 관례).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print(report)
    if args.out:
        io.open(args.out, "w", encoding="utf-8").write(report + "\n")
        print(f"\n리포트 저장: {args.out}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
