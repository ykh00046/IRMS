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


# 읽은 시트 이름과 그 시트에서 새로 얻은 LOT 수 — 리포트에 반드시 출력한다.
# (어떤 탭을 실제로 읽었는지 눈으로 확인하지 못하면 '누락 0건'을 신뢰할 수 없다.)
_SHEETS_READ: list = []


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

        # ⚠ 활성 탭 하나만 읽으면 안 된다 — 백업 시트는 월별 탭으로 나뉘어 있는 경우가
        # 흔하고, 그때 다른 탭의 누락 LOT 을 통째로 못 본 채 '누락 0건'을 선언하게 된다.
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for ws in wb.worksheets:
            before = len(lots)
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
            _SHEETS_READ.append((ws.title, len(lots) - before))
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
        _SHEETS_READ.append(("(CSV)", len(lots)))
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

    undated = 0
    if args.since:
        # 날짜가 비어 있는 행은 '기간 밖'이 아니라 '날짜 불명'이다. 예전에는 ""가 비교에서
        # 탈락해 조용히 제외됐는데, 수기 시트에서 날짜 칸 누락은 흔하고 그 행이 바로
        # 기입 누락일 확률이 높다 — 즉 잡아야 할 것을 필터가 지웠다. 항상 포함한다.
        filtered = OrderedDict()
        for lot, info in sheet_lots.items():
            date10 = (info["first_date"] or "")[:10]
            if not date10:
                undated += 1
                filtered[lot] = info
            elif date10 >= args.since:
                filtered[lot] = info
        sheet_lots = filtered

    missing, canceled, draft, ok = [], [], [], 0
    for lot, info in sheet_lots.items():
        status = brm.get(lot)
        if status is None:
            missing.append((lot, info["first_date"]))
        elif status == "canceled":
            canceled.append((lot, info["first_date"]))
        elif status == "draft":
            # 저장이 끝나지 않은 미완성 기록 — '있다'로 세면 진짜 누락을 가린다.
            draft.append((lot, info["first_date"]))
        else:
            ok += 1

    # 역방향(BRM 에만 있는 LOT) — 판정에는 넣지 않지만 반드시 보여준다. 작업자가 LOT 을
    # 오타로 넣으면 시트 쪽 '누락 1건'과 BRM 쪽 '여분 1건'이 쌍으로 생기는데, 여분을
    # 감추면 운영자가 진짜 누락으로 오판해 없는 데이터를 새로 만들게 된다.
    extra = sorted(set(brm.keys()) - set(sheet_lots.keys()))

    lines = []
    lines.append("=== 시트 ↔ BRM 제품 LOT 대조 리포트 ===")
    lines.append("읽은 시트: " + (", ".join(f"{n}({c}건)" for n, c in _SHEETS_READ) or "(없음)"))
    lines.append(f"시트 LOT: {len(sheet_lots)}건"
                 + (f" (작업일시 {args.since} 이후 + 날짜 불명 {undated}건 포함)" if args.since else ""))
    lines.append(f"BRM LOT: {len(brm)}건")
    lines.append(f"정상(BRM 존재): {ok}건 · 취소됨: {len(canceled)}건 · "
                 f"미완성(draft): {len(draft)}건 · 누락: {len(missing)}건")
    if missing:
        lines.append("")
        lines.append("--- 누락 (시트에 있으나 BRM 에 없음 — 확인 필수) ---")
        for lot, date in missing:
            lines.append(f"  {lot}  (작업일시 {date or '-'})")
    if draft:
        lines.append("")
        lines.append("--- 미완성 draft (저장이 끝나지 않음 — 확인 필수) ---")
        for lot, date in draft:
            lines.append(f"  {lot}  (작업일시 {date or '-'})")
    if canceled:
        lines.append("")
        lines.append("--- 취소됨 (BRM 에서 취소 처리 — 의도 확인) ---")
        for lot, date in canceled:
            lines.append(f"  {lot}  (작업일시 {date or '-'})")
    if extra:
        lines.append("")
        lines.append(f"--- 참고: BRM 에만 있는 LOT {len(extra)}건 (오타 대응쌍 확인용) ---")
        for lot in extra[:50]:
            lines.append(f"  {lot}")
        if len(extra) > 50:
            lines.append(f"  … 외 {len(extra) - 50}건")

    # 파싱이 실패했는데 '누락 0건'을 선언하는 것이 가장 위험하다(잘못된 탭·빈 CSV·컬럼
    # 밀림 모두 시트 0건으로 끝난다). 시트를 못 읽었으면 안전 선언 대신 강제 실패한다.
    parse_suspect = len(sheet_lots) == 0 or (brm and len(sheet_lots) < len(brm) * 0.5)
    if parse_suspect:
        lines.append("")
        lines.append(">>> [중단] 시트 파싱 실패 의심 — 시트 LOT 건수가 0이거나 BRM 대비 "
                     "현저히 적습니다. 위 '읽은 시트' 목록과 파일/탭을 확인하세요. "
                     "이 상태의 결과는 전환 근거로 쓸 수 없습니다.")
    elif not missing and not draft and not canceled:
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
    if parse_suspect:
        return 2
    return 1 if (missing or draft) else 0


if __name__ == "__main__":
    raise SystemExit(main())
