"""원료배합일지(DHR) 공식 양식 출력 검증."""

import io

import openpyxl

from src.services import dhr_excel


def _sample_record():
    return {
        "product_lot": "제품A260625",
        "worker": "홍길동",
        "work_date": "2026-06-25",
        "work_time": "10:00:00",
        "scale": "M-65",
        "total_amount": 1000,
        "details": [
            {"material_name": "HEMA", "material_lot": "MN-101", "ratio": 71.43,
             "theory_amount": 714.3, "actual_amount": 714.3},
            {"material_name": "NVP", "material_lot": "MN-102", "ratio": 28.57,
             "theory_amount": 285.7, "actual_amount": 285.7},
        ],
    }


def test_official_dhr_form_fills_template():
    xb = dhr_excel.build_official_dhr_xlsx(_sample_record())
    ws = openpyxl.load_workbook(io.BytesIO(xb)).active

    # 양식 제목·헤더 보존
    assert ws["A1"].value == "원 료 배 합 일 지"
    assert ws["C5"].value == "배합원료명"

    # 메타 채움
    assert "2026-06-25" in ws["A3"].value
    assert "홍길동" in ws["C3"].value
    assert "M-65" in ws["A4"].value

    # 제품 LOT + 총량/100
    assert ws["A6"].value == "제품A260625"
    assert ws["B6"].value == 10  # 1000 / 100

    # 자재 데이터(6행~)
    assert ws["C6"].value == "HEMA"
    assert ws["D6"].value == "MN-101"
    assert ws["E6"].value == 71.43
    assert ws["F6"].value == 714.3
    assert ws["G6"].value == 714.3
    assert ws["C7"].value == "NVP"

    # A/B 데이터 행 병합
    merged = {str(r) for r in ws.merged_cells.ranges}
    assert "A6:A7" in merged
    assert "B6:B7" in merged


def test_official_dhr_form_handles_missing_optionals():
    rec = _sample_record()
    rec["scale"] = None
    rec["work_time"] = None
    xb = dhr_excel.build_official_dhr_xlsx(rec, include_work_time=False)
    ws = openpyxl.load_workbook(io.BytesIO(xb)).active
    assert ws["A1"].value == "원 료 배 합 일 지"
    assert ws["A6"].value == "제품A260625"
