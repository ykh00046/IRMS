"""운영 대시보드 보고서 Excel 출력 — Program-estimation v3 dashboard_exporter 이식.

요약(KPI)·자재 사용량 TOP·작업자 통계를 한 시트로. openpyxl 만 의존(서버 동작).
PDF 변환(win32com)은 웹 부적합이라 제외.
"""

import io
import sqlite3
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font


def _section(ws, row: int, title: str, headers: list[str] | None, rows: list[list[Any]]) -> int:
    bold = Font(bold=True)
    ws.cell(row=row, column=1, value=title).font = bold
    row += 1
    if headers:
        for col, h in enumerate(headers, start=1):
            ws.cell(row=row, column=col, value=h).font = bold
        row += 1
    for data in rows:
        for col, val in enumerate(data, start=1):
            ws.cell(row=row, column=col, value=val)
        row += 1
    return row + 1  # 섹션 뒤 빈 줄


def build_dashboard_excel(
    connection: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
    from_ts: str,
    to_ts: str,
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "대시보드"
    ws.cell(row=1, column=1, value="운영 대시보드 보고서").font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=f"기간: {from_date} ~ {to_date}")

    # 요약
    completed = connection.execute(
        "SELECT COUNT(*) AS c FROM recipes WHERE status='completed' AND completed_at IS NOT NULL "
        "AND completed_at BETWEEN ? AND ?",
        (from_ts, to_ts),
    ).fetchone()["c"]
    meas = connection.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(value_weight),0) AS w FROM recipe_items "
        "WHERE measured_at IS NOT NULL AND measured_at BETWEEN ? AND ?",
        (from_ts, to_ts),
    ).fetchone()

    row = 4
    row = _section(ws, row, "[요약]", None, [
        ["완료 레시피", int(completed or 0)],
        ["계량 단계", int(meas["c"] or 0)],
        ["총 사용량(g)", round(float(meas["w"] or 0), 2)],
    ])

    # 자재 사용량 TOP 10
    mat_rows = connection.execute(
        "SELECT m.name AS name, m.category AS cat, COALESCE(SUM(ri.value_weight),0) AS w, COUNT(*) AS c "
        "FROM recipe_items ri JOIN materials m ON m.id=ri.material_id "
        "WHERE ri.measured_at IS NOT NULL AND ri.measured_at BETWEEN ? AND ? "
        "GROUP BY m.id, m.name, m.category ORDER BY w DESC LIMIT 10",
        (from_ts, to_ts),
    ).fetchall()
    row = _section(
        ws, row, "[자재 사용량 TOP 10]", ["자재", "분류", "총량(g)", "사용 횟수"],
        [[r["name"], r["cat"] or "", round(float(r["w"] or 0), 2), int(r["c"])] for r in mat_rows],
    )

    # 작업자 통계
    op_rows = connection.execute(
        "SELECT COALESCE(NULLIF(measured_by,''),'(미기록)') AS op, COUNT(*) AS c, "
        "COALESCE(SUM(value_weight),0) AS w FROM recipe_items "
        "WHERE measured_at IS NOT NULL AND measured_at BETWEEN ? AND ? GROUP BY op ORDER BY c DESC",
        (from_ts, to_ts),
    ).fetchall()
    row = _section(
        ws, row, "[작업자 통계]", ["작업자", "계량 건수", "총량(g)"],
        [[r["op"], int(r["c"]), round(float(r["w"] or 0), 2)] for r in op_rows],
    )

    widths = [22, 14, 14, 12]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + col)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
