"""발주서 생성·조회·수정·출력 서비스.

``forecast_service.compute_forecast()`` 의 발주 권장(urgent/soon) 자재를 입력으로
받아 발주서를 **스냅샷**으로 저장한다. 생성 이후 재고가 변동해도 발주서 항목은
생성 시점 값을 유지한다(발주 결정의 보존). 출력은 Excel(openpyxl) + 인쇄용 HTML.

Plan:   docs/01-plan/features/order-sheet-erp.plan.md
Design: docs/02-design/features/order-sheet-erp.design.md
"""

from __future__ import annotations

import io
import sqlite3
from datetime import date
from typing import Any

from ..db import row_to_dict, utc_now_text
from . import forecast_service

_URGENCY_LABEL = {"urgent": "긴급", "soon": "임박"}
_STATUS_LABEL = {
    "draft": "작성중",
    "sent": "전송됨",
    "failed": "실패",
    "cancelled": "취소",
}
_RECEIPT_STATUS_LABEL = {
    "pending": "미입고",
    "partial": "부분입고",
    "received": "입고완료",
}


class OrderStateError(Exception):
    """발주서 상태 전이 위반 (예: sent 발주서 수정/취소)."""


def _xlsx_safe(value: Any) -> Any:
    """스프레드시트 수식 인젝션 방어: 위험 문자로 시작하는 텍스트는 ' 접두."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def generate_order_no(connection: sqlite3.Connection, today: date | None = None) -> str:
    """``PO-YYYYMMDD-NNN`` 채번. 같은 날짜의 마지막 일련번호 + 1."""
    today = today or date.today()
    prefix = f"PO-{today.strftime('%Y%m%d')}-"
    row = connection.execute(
        "SELECT order_no FROM purchase_orders WHERE order_no LIKE ? "
        "ORDER BY order_no DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(str(row["order_no"]).rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{prefix}{seq:03d}"


def create_order_from_forecast(
    connection: sqlite3.Connection,
    *,
    window_days: int,
    created_by: str,
    today: date | None = None,
) -> dict[str, Any]:
    """forecast 발주 권장(urgent/soon) 자재를 스냅샷으로 발주서(draft) 생성.

    권장 자재가 0건이면 :class:`ValueError`.
    """
    result = forecast_service.compute_forecast(connection, window_days=window_days)
    reorder = [it for it in result["items"] if it["status"] in ("urgent", "soon")]
    if not reorder:
        raise ValueError("발주 권장 자재가 없습니다.")

    now = utc_now_text()
    order_no = generate_order_no(connection, today)
    cursor = connection.execute(
        """
        INSERT INTO purchase_orders
            (order_no, status, window_days, item_count, total_qty, created_by, created_at)
        VALUES (?, 'draft', ?, 0, 0, ?, ?)
        """,
        (order_no, window_days, created_by, now),
    )
    order_id = int(cursor.lastrowid)

    total = 0.0
    for it in reorder:
        qty = float(it["recommended_order_qty"] or 0)
        total += qty
        connection.execute(
            """
            INSERT INTO purchase_order_items
                (order_id, material_id, material_name, category, unit, stock_quantity,
                 avg_daily, days_remaining, predicted_stockout_date, urgency_status,
                 recommended_qty, order_qty)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                it["material_id"],
                it["name"],
                it["category"],
                it["unit"],
                it["stock_quantity"],
                it["avg_daily"],
                it["days_remaining"],
                it["predicted_stockout_date"],
                it["status"],
                qty,
                qty,
            ),
        )

    connection.execute(
        "UPDATE purchase_orders SET item_count = ?, total_qty = ? WHERE id = ?",
        (len(reorder), total, order_id),
    )
    order = get_order(connection, order_id)
    assert order is not None
    return order


def list_orders(connection: sqlite3.Connection, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, order_no, status, receipt_status, window_days, item_count, total_qty,
               created_by, created_at, sent_at, erp_mode
        FROM purchase_orders
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    orders = []
    for row in rows:
        payload = row_to_dict(row)
        payload["status_label"] = _STATUS_LABEL.get(payload["status"], payload["status"])
        receipt_status = payload.get("receipt_status") or "pending"
        payload["receipt_status"] = receipt_status
        payload["receipt_status_label"] = _RECEIPT_STATUS_LABEL.get(
            receipt_status, receipt_status
        )
        orders.append(payload)
    return orders


def get_order(connection: sqlite3.Connection, order_id: int) -> dict[str, Any] | None:
    header = connection.execute(
        "SELECT * FROM purchase_orders WHERE id = ?", (order_id,)
    ).fetchone()
    if header is None:
        return None
    order = row_to_dict(header)
    order["status_label"] = _STATUS_LABEL.get(order["status"], order["status"])
    _receipt = order.get("receipt_status") or "pending"
    order["receipt_status"] = _receipt
    order["receipt_status_label"] = _RECEIPT_STATUS_LABEL.get(_receipt, _receipt)
    item_rows = connection.execute(
        "SELECT * FROM purchase_order_items WHERE order_id = ? ORDER BY id",
        (order_id,),
    ).fetchall()
    items = []
    for r in item_rows:
        item = row_to_dict(r)
        item["urgency_label"] = _URGENCY_LABEL.get(
            item.get("urgency_status"), item.get("urgency_status") or ""
        )
        items.append(item)
    order["items"] = items
    return order


def _recompute_totals(connection: sqlite3.Connection, order_id: int) -> tuple[int, float]:
    row = connection.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(order_qty), 0) AS t "
        "FROM purchase_order_items WHERE order_id = ? AND order_qty > 0",
        (order_id,),
    ).fetchone()
    return int(row["c"]), float(row["t"])


def update_order(
    connection: sqlite3.Connection,
    order_id: int,
    *,
    note: str | None = None,
    items: list[dict[str, Any]] | None = None,
    now: str | None = None,
) -> dict[str, Any] | None:
    """draft 발주서의 수량/비고 수정. draft 가 아니면 :class:`OrderStateError`."""
    order = get_order(connection, order_id)
    if order is None:
        return None
    if order["status"] != "draft":
        raise OrderStateError("작성중인 발주서만 수정할 수 있습니다.")

    now = now or utc_now_text()
    valid_ids = {it["id"] for it in order["items"]}
    for edit in items or []:
        item_id = int(edit["id"])
        if item_id not in valid_ids:
            continue
        qty = float(edit.get("order_qty", 0) or 0)
        if qty < 0:
            raise ValueError("발주 수량은 0 이상이어야 합니다.")
        connection.execute(
            "UPDATE purchase_order_items SET order_qty = ?, note = ? "
            "WHERE id = ? AND order_id = ?",
            (qty, edit.get("note"), item_id, order_id),
        )

    item_count, total = _recompute_totals(connection, order_id)
    new_note = note if note is not None else order["note"]
    connection.execute(
        "UPDATE purchase_orders SET item_count = ?, total_qty = ?, note = ?, updated_at = ? "
        "WHERE id = ?",
        (item_count, total, new_note, now, order_id),
    )
    return get_order(connection, order_id)


def cancel_order(
    connection: sqlite3.Connection, order_id: int, *, now: str | None = None
) -> dict[str, Any] | None:
    """draft/failed 발주서 취소. sent 는 거부(:class:`OrderStateError`)."""
    order = get_order(connection, order_id)
    if order is None:
        return None
    if order["status"] not in ("draft", "failed"):
        raise OrderStateError("작성중/실패 상태의 발주서만 취소할 수 있습니다.")
    now = now or utc_now_text()
    connection.execute(
        "UPDATE purchase_orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (now, order_id),
    )
    return get_order(connection, order_id)


def mark_sent(
    connection: sqlite3.Connection,
    order_id: int,
    *,
    result: Any,
    sent_by: str,
    now: str | None = None,
) -> dict[str, Any] | None:
    """ERP 전송 결과(:class:`erp_client.ErpResult`)를 반영. ok→sent, 실패→failed."""
    order = get_order(connection, order_id)
    if order is None:
        return None
    now = now or utc_now_text()
    status = "sent" if result.ok else "failed"
    connection.execute(
        """
        UPDATE purchase_orders
        SET status = ?, sent_at = ?, sent_by = ?, erp_mode = ?,
            erp_status_code = ?, erp_response = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, now, sent_by, result.mode, result.status_code, result.body, now, order_id),
    )
    return get_order(connection, order_id)


def order_payload(order: dict[str, Any]) -> dict[str, Any]:
    """ERP 전송/인쇄 공용 직렬화. order_qty>0 항목만 포함."""
    items = [
        {
            "material_id": it["material_id"],
            "material_name": it["material_name"],
            "category": it["category"],
            "unit": it["unit"],
            "order_qty": it["order_qty"],
            "note": it["note"],
        }
        for it in order["items"]
        if (it["order_qty"] or 0) > 0
    ]
    return {
        "order_no": order["order_no"],
        "created_at": order["created_at"],
        "created_by": order["created_by"],
        "note": order["note"],
        "item_count": len(items),
        "total_qty": sum(i["order_qty"] for i in items),
        "items": items,
    }


def build_workbook(order: dict[str, Any]) -> bytes:
    """발주서를 .xlsx 바이트로 생성."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "발주서"

    ws.append(["발주서"])
    ws.append(["발주번호", order["order_no"]])
    ws.append(["작성일", order["created_at"]])
    ws.append(["작성자", _xlsx_safe(order["created_by"])])
    ws.append(["비고", _xlsx_safe(order["note"] or "")])
    ws.append([])

    ws.append(
        ["원재료명", "카테고리", "단위", "권장량", "발주량", "예상 소진일", "긴급도", "비고"]
    )
    total = 0.0
    for it in order["items"]:
        if (it["order_qty"] or 0) <= 0:
            continue
        total += float(it["order_qty"])
        ws.append(
            [
                _xlsx_safe(it["material_name"]),
                _xlsx_safe(it["category"] or ""),
                it["unit"],
                it["recommended_qty"],
                it["order_qty"],
                it["predicted_stockout_date"] or "",
                _URGENCY_LABEL.get(it["urgency_status"], it["urgency_status"] or ""),
                _xlsx_safe(it["note"] or ""),
            ]
        )
    ws.append(["합계", "", "", "", total])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
