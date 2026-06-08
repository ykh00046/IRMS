"""Material LOT / shelf-life (expiry) tracking service.

Tracks incoming material in LOT units with a received date and an optional
expiry date, independent of ``materials.stock_quantity`` and the measurement
deduction path (``stock_service``). This is a traceability / expiry-alert
layer only — it does NOT modify the measurement-driven stock source of truth.

All write operations log nothing on their own; the caller owns the
transaction and commits. Pure status helpers (``expiry_state`` /
``days_until``) are split out for direct unit testing.

Plan:   docs/01-plan/features/lot-expiry-tracking.plan.md
Design: docs/02-design/features/lot-expiry-tracking.design.md
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from ..db import utc_now_text

DEFAULT_ALERT_DAYS = 30
VALID_STATUSES = {"active", "depleted", "discarded"}

# 만료 위험 우선 정렬용 (작을수록 위에)
_STATE_ORDER = {"expired": 0, "expiring_soon": 1, "ok": 2, "no_expiry": 3}


def _today() -> date:
    return date.today()


def _parse_date(value: str | None, *, field: str) -> str | None:
    """ISO(YYYY-MM-DD) 문자열 검증 후 정규화. None은 그대로 통과."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ValueError(f"{field}는 YYYY-MM-DD 형식이어야 합니다.")


# ── 순수 판정 함수 ─────────────────────────────────────────────


def expiry_state(
    expiry_date: str | None, today: date, alert_days: int = DEFAULT_ALERT_DAYS
) -> str:
    """'expired' | 'expiring_soon' | 'ok' | 'no_expiry' (순수)."""
    if not expiry_date:
        return "no_expiry"
    exp = date.fromisoformat(expiry_date)
    if exp < today:
        return "expired"
    if exp <= today + timedelta(days=alert_days):
        return "expiring_soon"
    return "ok"


def days_until(expiry_date: str | None, today: date) -> int | None:
    """오늘부터 유통기한까지 남은 일수(음수=경과). 무기한이면 None."""
    if not expiry_date:
        return None
    return (date.fromisoformat(expiry_date) - today).days


def _decorate(row: dict[str, Any], today: date, alert_days: int) -> dict[str, Any]:
    exp = row.get("expiry_date")
    row["expiry_state"] = expiry_state(exp, today, alert_days)
    row["days_until"] = days_until(exp, today)
    return row


# ── 쓰기 연산 (caller commits) ─────────────────────────────────


def register_lot(
    connection: sqlite3.Connection,
    *,
    material_id: int,
    lot_no: str | None,
    quantity: float,
    received_at: str | None,
    expiry_date: str | None,
    actor: dict[str, Any] | None,
    note: str | None = None,
) -> dict[str, Any]:
    """입고 LOT 등록. 잔여=입고수량, status=active."""
    if quantity is None or quantity <= 0:
        raise ValueError("입고 수량은 0보다 커야 합니다.")
    received = _parse_date(received_at, field="입고일") or _today().isoformat()
    expiry = _parse_date(expiry_date, field="유통기한")
    if expiry is not None and expiry < received:
        raise ValueError("유통기한은 입고일 이후여야 합니다.")

    actor_id = actor.get("id") if actor else None
    actor_name = (actor.get("display_name") or actor.get("username")) if actor else None
    cursor = connection.execute(
        """
        INSERT INTO material_lots
            (material_id, lot_no, received_quantity, remaining_quantity,
             received_at, expiry_date, status, note, actor_id, actor_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            material_id,
            (lot_no or None),
            quantity,
            quantity,
            received,
            expiry,
            note,
            actor_id,
            actor_name,
            utc_now_text(),
        ),
    )
    return {
        "lot_id": int(cursor.lastrowid),
        "material_id": material_id,
        "remaining_quantity": quantity,
        "received_at": received,
        "expiry_date": expiry,
        "status": "active",
    }


def _get_active_lot(connection: sqlite3.Connection, lot_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT id, material_id, remaining_quantity, status FROM material_lots WHERE id = ?",
        (lot_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"LOT을 찾을 수 없습니다: {lot_id}")
    if row["status"] != "active":
        raise ValueError("이미 소진/폐기된 LOT입니다.")
    return row


def consume_lot(
    connection: sqlite3.Connection,
    *,
    lot_id: int,
    amount: float,
    actor: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """LOT 잔여에서 amount 소진. 잔여 0 이하면 depleted 전환."""
    if amount is None or amount <= 0:
        raise ValueError("소진량은 0보다 커야 합니다.")
    row = _get_active_lot(connection, lot_id)
    remaining = float(row["remaining_quantity"])
    if amount > remaining + 1e-9:
        raise ValueError(f"소진량이 잔여({remaining:g})를 초과합니다.")
    new_remaining = round(remaining - amount, 6)
    new_status = "depleted" if new_remaining <= 1e-9 else "active"
    if new_status == "depleted":
        new_remaining = 0.0
    connection.execute(
        "UPDATE material_lots SET remaining_quantity = ?, status = ? WHERE id = ?",
        (new_remaining, new_status, lot_id),
    )
    return {"lot_id": lot_id, "remaining_quantity": new_remaining, "status": new_status}


def discard_lot(
    connection: sqlite3.Connection,
    *,
    lot_id: int,
    actor: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """LOT 전량 폐기. 폐기 사유 필수."""
    if not note or not str(note).strip():
        raise ValueError("폐기 사유가 필요합니다.")
    _get_active_lot(connection, lot_id)
    connection.execute(
        "UPDATE material_lots SET status = 'discarded', remaining_quantity = 0 WHERE id = ?",
        (lot_id,),
    )
    return {"lot_id": lot_id, "remaining_quantity": 0.0, "status": "discarded"}


# ── 조회 / 집계 ────────────────────────────────────────────────


def list_lots(
    connection: sqlite3.Connection,
    *,
    material_id: int | None = None,
    include_inactive: bool = False,
    alert_days: int = DEFAULT_ALERT_DAYS,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """LOT 목록 + 유통기한 상태. 만료 위험 우선 → 유통기한 오름차순."""
    today = today or _today()
    clauses: list[str] = []
    params: list[Any] = []
    if material_id is not None:
        clauses.append("ml.material_id = ?")
        params.append(material_id)
    if not include_inactive:
        clauses.append("ml.status = 'active'")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = connection.execute(
        f"""
        SELECT ml.id, ml.material_id, m.name AS material_name, m.category,
               ml.lot_no, ml.received_quantity, ml.remaining_quantity,
               ml.received_at, ml.expiry_date, ml.status, ml.note,
               ml.actor_name, ml.created_at
        FROM material_lots ml
        LEFT JOIN materials m ON m.id = ml.material_id
        {where}
        """,
        params,
    ).fetchall()

    items = [_decorate(dict(r), today, alert_days) for r in rows]
    items.sort(
        key=lambda it: (
            _STATE_ORDER.get(it["expiry_state"], 9),
            it["days_until"] if it["days_until"] is not None else float("inf"),
            it.get("material_name") or "",
        )
    )
    return items


def expiry_alert(
    connection: sqlite3.Connection,
    *,
    alert_days: int = DEFAULT_ALERT_DAYS,
    limit: int = 5,
    today: date | None = None,
) -> dict[str, Any]:
    """만료/임박 LOT만 추려 대시보드 알림용으로 압축.

    active & 잔여>0 인 LOT 중 expired/expiring_soon 만 노출. 만료 먼저,
    임박일(days_until) 오름차순. ok/무기한은 제외.

    Design: docs/02-design/features/lot-expiry-tracking.design.md §3.3
    """
    today = today or _today()
    rows = connection.execute(
        """
        SELECT ml.id, ml.material_id, m.name AS material_name, m.category,
               ml.lot_no, ml.remaining_quantity, ml.expiry_date
        FROM material_lots ml
        LEFT JOIN materials m ON m.id = ml.material_id
        WHERE ml.status = 'active'
          AND ml.remaining_quantity > 0
          AND ml.expiry_date IS NOT NULL
        """
    ).fetchall()

    alerts = []
    for r in rows:
        item = _decorate(dict(r), today, alert_days)
        if item["expiry_state"] in ("expired", "expiring_soon"):
            alerts.append(item)

    alerts.sort(
        key=lambda it: (
            _STATE_ORDER.get(it["expiry_state"], 9),
            it["days_until"] if it["days_until"] is not None else float("inf"),
        )
    )

    expired = sum(1 for it in alerts if it["expiry_state"] == "expired")
    expiring_soon = sum(1 for it in alerts if it["expiry_state"] == "expiring_soon")
    shown = alerts[:limit]
    return {
        "alert_days": alert_days,
        "expired": expired,
        "expiring_soon": expiring_soon,
        "total_alert": len(alerts),
        "shown": len(shown),
        "items": [
            {
                "lot_id": it["id"],
                "material_id": it["material_id"],
                "material_name": it["material_name"],
                "category": it["category"],
                "lot_no": it["lot_no"],
                "remaining_quantity": it["remaining_quantity"],
                "expiry_date": it["expiry_date"],
                "days_until": it["days_until"],
                "expiry_state": it["expiry_state"],
            }
            for it in shown
        ],
    }
