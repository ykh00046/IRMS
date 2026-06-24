"""원료배합일지(DHR) 공식 양식 Excel 출력.

C:\\X\\Program-estimation v3 의 ExcelWriter 를 웹용으로 이식한 것.
공식 양식(src/resources/dhr_template.xlsx, "원 료 배 합 일 지")을 복사·채워서
규제 양식과 동일한 배합일지를 출력한다. openpyxl 만 의존(서버에서 동작).

양식 셀 매핑(원본 config/settings.CELL_MAPPING 동일):
  날짜 A3 · 저울 A4 · 작업자 C3 · 작업시간 E3 · 제품LOT A6 · 총량/100 B6
  데이터 6행~ : 배합원료명 C · 원료LOT D · 배합비율 E · 배합량(g) F · 실제배합량(g) G
서명(작성/검토/승인)은 이미지 합성 단계(별도)에서 올린다.
"""

import io
import os
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Side

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "..", "resources", "dhr_template.xlsx")

CELL_MAPPING = {
    "date": "A3",
    "scale": "A4",
    "worker": "C3",
    "work_time": "E3",
    "product_lot": "A6",
    "total_amount": "B6",
    "data_start_row": 6,
    "material_name_col": "C",
    "material_lot_col": "D",
    "ratio_col": "E",
    "theory_amount_col": "F",
    "actual_amount_col": "G",
}

_THIN = Side(style="thin", color="000000")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center")


def build_official_dhr_xlsx(record: dict[str, Any], *, include_work_time: bool = True) -> bytes:
    """배합 기록 dict → 원료배합일지 공식 양식 xlsx 바이트.

    record: get_blend_record 반환(product_lot/worker/work_date/work_time/scale/
            total_amount/details[material_name,material_lot,ratio,theory_amount,actual_amount]).
    """
    wb = load_workbook(TEMPLATE_PATH)
    ws = wb.active
    m = CELL_MAPPING

    ws[m["date"]] = f"작업일: {record.get('work_date', '')}"
    ws[m["scale"]] = f"저울: {record.get('scale') or ''}"
    ws[m["worker"]] = f"작업자 : {record.get('worker', '')}"
    if include_work_time:
        ws[m["work_time"]] = f"작업시간 : {record.get('work_time') or ''}"
    ws[m["product_lot"]] = record.get("product_lot", "")
    ws[m["total_amount"]] = (record.get("total_amount") or 0) / 100

    details = record.get("details", []) or []
    start = m["data_start_row"]
    for i, d in enumerate(details):
        row = start + i
        ws[f"{m['material_name_col']}{row}"] = d.get("material_name", "")
        ws[f"{m['material_lot_col']}{row}"] = d.get("material_lot") or ""
        ws[f"{m['ratio_col']}{row}"] = d.get("ratio")
        ws[f"{m['theory_amount_col']}{row}"] = d.get("theory_amount")
        ws[f"{m['actual_amount_col']}{row}"] = d.get("actual_amount")

    end_row = start + max(len(details), 1) - 1

    # 제품 LOT(A)·배합량100g(B)을 데이터 행 전체에 걸쳐 병합 (원본 동작)
    if end_row > start:
        ws.merge_cells(f"A{start}:A{end_row}")
        ws.merge_cells(f"B{start}:B{end_row}")
    ws[f"A{start}"].alignment = _CENTER
    ws[f"B{start}"].alignment = _CENTER

    # 데이터 영역 테두리·정렬 (양식 기본 행 범위를 벗어나도 적용)
    for row in range(start, end_row + 1):
        for col in range(1, 8):
            cell = ws.cell(row=row, column=col)
            cell.border = _BORDER
            cell.alignment = _CENTER

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
