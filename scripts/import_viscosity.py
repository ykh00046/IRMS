"""합성 점도.xlsx → viscosity_readings 임포트 (멱등).

엑셀 시트(PB/SBCT/SCRA)를 같은 코드의 viscosity_products 에 매핑하여 LOT별
점도 측정을 적재한다. (product_id, lot_no) UNIQUE 인덱스 덕분에 재실행해도
중복은 건너뛴다.

운영 DB 에 적재하려면 서버와 동일한 환경변수로 실행:
    set IRMS_DATA_DIR=...   (서버가 쓰는 데이터 디렉토리)
    python scripts/import_viscosity.py "합성 점도.xlsx"

시트 컬럼 규약 (4행부터 데이터):
    A=LOT  B=점도  C=메모(열1)  D=비고(SCRA)  E=레시피/원료  F=원료 LOT
"""

import sqlite3
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import load_workbook  # noqa: E402

from src.db import get_connection, init_db, utc_now_text  # noqa: E402
from src.services import viscosity_service  # noqa: E402


def _as_text(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "date"):  # datetime → 날짜 ISO
        return value.date().isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def run(path: str) -> int:
    init_db()
    wb = load_workbook(path, data_only=True)
    now = utc_now_text()
    total = skipped = 0

    with get_connection() as connection:
        for sheet in wb.worksheets:
            code = sheet.title.strip()
            product = viscosity_service.get_product_by_code(connection, code)
            if not product:
                print(f"[skip] 제품 없음: {code}")
                continue

            sheet_count = 0
            for row in range(4, sheet.max_row + 1):
                lot = sheet.cell(row, 1).value
                visc = sheet.cell(row, 2).value
                lot_no = _as_text(lot)
                if not lot_no or not isinstance(visc, (int, float)):
                    continue

                memo_parts = []
                for col in (3, 4):
                    if sheet.max_column >= col:
                        part = _as_text(sheet.cell(row, col).value)
                        if part:
                            memo_parts.append(part)
                memo = " / ".join(memo_parts) if memo_parts else None
                recipe = _as_text(sheet.cell(row, 5).value) if sheet.max_column >= 5 else None
                material_lot = _as_text(sheet.cell(row, 6).value) if sheet.max_column >= 6 else None

                try:
                    viscosity_service.add_reading(
                        connection,
                        product_id=product["id"],
                        lot_no=lot_no,
                        viscosity=float(visc),
                        measured_date=None,  # LOT 에서 추론
                        memo=memo,
                        recipe_material=recipe,
                        material_lot=material_lot,
                        created_by="엑셀 임포트",
                        created_at=now,
                    )
                    total += 1
                    sheet_count += 1
                except sqlite3.IntegrityError:
                    skipped += 1
            print(f"[{code}] {sheet_count}건 적재")
        connection.commit()

    print(f"완료: 신규 {total}건, 중복 건너뜀 {skipped}건")
    return total


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "합성 점도.xlsx"
    run(src)
