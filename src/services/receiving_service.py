"""발주 입고·검수 오케스트레이션 서비스.

ERP 로 전송된 발주서(``status='sent'``)에 대해 물품이 실제 도착하면 입고를 등록한다.
입고된 항목마다 ``lot_service.register_lot()`` 로 LOT/유통기한을 만들고
``stock_service.restock()`` 로 재고를 증가시키되, **둘을 같은 트랜잭션에서** 처리해
한쪽만 반영되는 일이 없도록 한다 (caller commits).

ERP 전송 상태(``purchase_orders.status``)는 건드리지 않고, 입고 진행은 직교 축인
``purchase_orders.receipt_status`` (pending/partial/received) 로 추적한다.

Plan:   docs/01-plan/features/purchase-order-receiving.plan.md
Design: docs/02-design/features/purchase-order-receiving.design.md
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from ..db import row_to_dict, utc_now_text
from . import lot_service, stock_service

RECEIPT_STATUS_LABEL = {
    "pending": "미입고",
    "partial": "부분입고",
    "received": "입고완료",
}


class ReceivingStateError(Exception):
    """입고 불가 상태(예: sent 아닌 발주서 입고 시도)."""


def generate_receipt_no(connection: sqlite3.Connection, today: date | None = None) -> str:
    """``RC-YYYYMMDD-NNN`` 채번. 같은 날짜의 마지막 일련번호 + 1."""
    today = today or date.today()
    prefix = f"RC-{today.strftime('%Y%m%d')}-"
    row = connection.execute(
        "SELECT receipt_no FROM po_receipts WHERE receipt_no LIKE ? "
        "ORDER BY receipt_no DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = 1
    if row:
        try:
            seq = int(str(row["receipt_no"]).rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{prefix}{seq:03d}"


def _recompute_receipt_status(connection: sqlite3.Connection, order_id: int) -> str:
    """발주 항목 입고 누적으로 receipt_status 판정.

    - 어떤 항목도 received_qty>0 아니면 'pending'
    - order_qty>0 항목이 존재하고 그 전부 received_qty>=order_qty 면 'received'
    - 그 외 'partial'
    """
    rows = connection.execute(
        "SELECT order_qty, received_qty FROM purchase_order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    any_received = any(float(r["received_qty"] or 0) > 0 for r in rows)
    if not any_received:
        return "pending"
    orderable = [r for r in rows if float(r["order_qty"] or 0) > 0]
    if orderable and all(
        float(r["received_qty"] or 0) + 1e-9 >= float(r["order_qty"] or 0)
        for r in orderable
    ):
        return "received"
    return "partial"


def receive_order(
    connection: sqlite3.Connection,
    *,
    order_id: int,
    lines: list[dict[str, Any]],
    received_by: str,
    actor: dict[str, Any] | None,
    note: str | None = None,
    now: str | None = None,
) -> dict[str, Any] | None:
    """발주서 입고 등록. 입고 항목마다 LOT 생성 + 재고 증가 + 이력 기록을 원자적으로 처리.

    ``lines``: ``[{order_item_id, received_qty, lot_no?, expiry_date?, note?}, …]``

    발주가 없으면 ``None``. ``status != 'sent'`` 면 :class:`ReceivingStateError`.
    입고할(received_qty>0) 항목이 하나도 없으면 :class:`ValueError`.
    caller 가 commit 을 소유한다.
    """
    header = connection.execute(
        "SELECT id, status FROM purchase_orders WHERE id = ?", (order_id,)
    ).fetchone()
    if header is None:
        return None
    if header["status"] != "sent":
        raise ReceivingStateError("전송된(sent) 발주서만 입고할 수 있습니다.")

    # 이 발주에 속한 유효 항목만 입고 대상으로 인정
    item_rows = connection.execute(
        "SELECT id, material_id, material_name FROM purchase_order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    items_by_id = {int(r["id"]): r for r in item_rows}

    prepared: list[dict[str, Any]] = []
    for line in lines or []:
        item_id = int(line["order_item_id"])
        item = items_by_id.get(item_id)
        if item is None:
            continue  # 다른 발주의 항목/잘못된 id 는 무시
        qty = float(line.get("received_qty", 0) or 0)
        if qty <= 0:
            continue  # 0/음수 입고는 건너뜀(분할 입고에서 정상)
        prepared.append(
            {
                "order_item_id": item_id,
                "material_id": int(item["material_id"]),
                "material_name": item["material_name"],
                "received_qty": qty,
                "lot_no": (line.get("lot_no") or None),
                "expiry_date": (line.get("expiry_date") or None),
                "note": (line.get("note") or None),
            }
        )

    if not prepared:
        raise ValueError("입고할 수량이 없습니다.")

    now = now or utc_now_text()
    receipt_no = generate_receipt_no(connection)
    total_qty = sum(p["received_qty"] for p in prepared)
    cursor = connection.execute(
        """
        INSERT INTO po_receipts
            (receipt_no, order_id, note, item_count, total_qty, received_by, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (receipt_no, order_id, note, len(prepared), total_qty, received_by, now),
    )
    receipt_id = int(cursor.lastrowid)

    result_lines = []
    for p in prepared:
        # ① LOT/유통기한 생성 — received_at 은 None 으로 두어 lot_service 가 오늘 날짜(date) 적용
        #    (now 는 datetime ISO 라 date.fromisoformat 에 직접 넘기면 안 됨)
        lot = lot_service.register_lot(
            connection,
            material_id=p["material_id"],
            lot_no=p["lot_no"],
            quantity=p["received_qty"],
            received_at=None,
            expiry_date=p["expiry_date"],
            actor=actor,
            note=p["note"],
        )
        # ② 재고 증가(정상 restock 경로 — material_stock_logs 기록 포함)
        stock = stock_service.restock(
            connection,
            material_id=p["material_id"],
            amount=p["received_qty"],
            actor=actor,
            note=f"발주 입고: {receipt_no}",
        )
        # ③ 입고 항목 이력(LOT/재고로그 연결)
        connection.execute(
            """
            INSERT INTO po_receipt_items
                (receipt_id, order_item_id, material_id, material_name, received_qty,
                 lot_no, expiry_date, lot_id, stock_log_id, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                p["order_item_id"],
                p["material_id"],
                p["material_name"],
                p["received_qty"],
                p["lot_no"],
                lot["expiry_date"],
                lot["lot_id"],
                stock["log_id"],
                p["note"],
            ),
        )
        # ④ 발주 항목 입고 누적
        connection.execute(
            "UPDATE purchase_order_items SET received_qty = received_qty + ? WHERE id = ?",
            (p["received_qty"], p["order_item_id"]),
        )
        result_lines.append(
            {
                "order_item_id": p["order_item_id"],
                "material_id": p["material_id"],
                "material_name": p["material_name"],
                "received_qty": p["received_qty"],
                "lot_id": lot["lot_id"],
                "stock_log_id": stock["log_id"],
                "expiry_date": lot["expiry_date"],
            }
        )

    receipt_status = _recompute_receipt_status(connection, order_id)
    connection.execute(
        "UPDATE purchase_orders SET receipt_status = ?, updated_at = ? WHERE id = ?",
        (receipt_status, now, order_id),
    )

    return {
        "receipt_id": receipt_id,
        "receipt_no": receipt_no,
        "order_id": order_id,
        "receipt_status": receipt_status,
        "receipt_status_label": RECEIPT_STATUS_LABEL.get(receipt_status, receipt_status),
        "item_count": len(prepared),
        "total_qty": total_qty,
        "lines": result_lines,
    }


def list_receipts(connection: sqlite3.Connection, order_id: int) -> list[dict[str, Any]]:
    """발주서의 입고 이력(헤더 + 항목)을 최신순으로 반환."""
    headers = connection.execute(
        """
        SELECT id, receipt_no, order_id, note, item_count, total_qty,
               received_by, received_at
        FROM po_receipts
        WHERE order_id = ?
        ORDER BY received_at DESC, id DESC
        """,
        (order_id,),
    ).fetchall()
    receipts = []
    for h in headers:
        receipt = row_to_dict(h)
        item_rows = connection.execute(
            """
            SELECT id, order_item_id, material_id, material_name, received_qty,
                   lot_no, expiry_date, lot_id, stock_log_id, note
            FROM po_receipt_items
            WHERE receipt_id = ?
            ORDER BY id
            """,
            (receipt["id"],),
        ).fetchall()
        receipt["items"] = [row_to_dict(r) for r in item_rows]
        receipts.append(receipt)
    return receipts
