"""Material consumption forecasting and reorder recommendation.

Reads the consumption history accumulated by material-stock-tracking
(``material_stock_logs`` rows with ``reason='measurement'`` and a negative
``delta``) and projects, per material:

    * average daily consumption over a trailing window
    * days remaining until stock-out at that rate
    * predicted stock-out date
    * recommended reorder quantity and an urgency status

All functions are pure reads except :func:`set_forecast_params`, which the
caller commits. No new tables — only the existing logs/materials are used.

Plan:   docs/01-plan/features/material-forecast.plan.md
Design: docs/02-design/features/material-forecast.design.md
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..db import utc_cutoff_text, utc_now_text

DEFAULT_WINDOW_DAYS = 30
DEFAULT_LEAD_TIME_DAYS = 7.0
DEFAULT_REORDER_CYCLE_DAYS = 30.0
SAFETY_FACTOR = 0.5  # "soon" 판정용 안전버퍼 (리드타임의 50%)

_STATUS_ORDER = {"urgent": 0, "soon": 1, "ok": 2, "no_data": 3}


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _consumption_by_material(
    connection: sqlite3.Connection, window_days: int
) -> dict[int, float]:
    """Sum of consumed weight per material over the trailing window."""
    cutoff = utc_cutoff_text(utc_now_text(), window_days * 86400)
    rows = connection.execute(
        """
        SELECT material_id, SUM(-delta) AS consumed
        FROM material_stock_logs
        WHERE reason = 'measurement'
          AND delta < 0
          AND created_at >= ?
        GROUP BY material_id
        """,
        (cutoff,),
    ).fetchall()
    return {int(r["material_id"]): float(r["consumed"] or 0) for r in rows}


def _forecast_one(
    *,
    stock: float,
    consumed: float,
    window_days: int,
    lead_time_days: float,
    reorder_cycle_days: float,
    today: date,
) -> dict[str, Any]:
    lead = lead_time_days if lead_time_days > 0 else DEFAULT_LEAD_TIME_DAYS
    cycle = reorder_cycle_days if reorder_cycle_days > 0 else DEFAULT_REORDER_CYCLE_DAYS
    avg_daily = consumed / window_days if window_days > 0 else 0.0

    if avg_daily <= 0:
        return {
            "avg_daily": 0.0,
            "consumed_in_window": consumed,
            "lead_time_days": lead,
            "reorder_cycle_days": cycle,
            "days_remaining": None,
            "predicted_stockout_date": None,
            "reorder_point": 0.0,
            "recommended_order_qty": 0.0,
            "status": "no_data",
        }

    days_remaining = stock / avg_daily
    reorder_point = avg_daily * lead
    recommended = max(0.0, avg_daily * cycle - stock)

    if days_remaining <= lead:
        status = "urgent"
    elif days_remaining <= lead * (1 + SAFETY_FACTOR):
        status = "soon"
    else:
        status = "ok"

    # 소진 예상일: 음수 잔여(이미 소진)면 오늘로 고정
    offset = max(0, int(days_remaining))
    stockout = today + timedelta(days=offset)

    return {
        "avg_daily": round(avg_daily, 4),
        "consumed_in_window": round(consumed, 4),
        "lead_time_days": lead,
        "reorder_cycle_days": cycle,
        "days_remaining": round(days_remaining, 1),
        "predicted_stockout_date": stockout.isoformat(),
        "reorder_point": round(reorder_point, 4),
        "recommended_order_qty": round(recommended, 4),
        "status": status,
    }


def compute_forecast(
    connection: sqlite3.Connection, *, window_days: int = DEFAULT_WINDOW_DAYS
) -> dict[str, Any]:
    """Forecast every active weight-type material.

    Returns ``{"params", "summary", "items"}``. count-type materials are
    excluded (Plan §7-3). Items are ordered urgent → soon → ok → no_data,
    then by ascending days_remaining.
    """
    consumption = _consumption_by_material(connection, window_days)
    today = _today_utc()

    rows = connection.execute(
        """
        SELECT id, name, category, unit, stock_quantity,
               lead_time_days, reorder_cycle_days
        FROM materials
        WHERE is_active = 1 AND unit_type = 'weight'
        ORDER BY name
        """
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        mid = int(row["id"])
        stock = float(row["stock_quantity"] or 0)
        forecast = _forecast_one(
            stock=stock,
            consumed=consumption.get(mid, 0.0),
            window_days=window_days,
            lead_time_days=float(row["lead_time_days"] or 0),
            reorder_cycle_days=float(row["reorder_cycle_days"] or 0),
            today=today,
        )
        items.append(
            {
                "material_id": mid,
                "name": row["name"],
                "category": row["category"],
                "unit": row["unit"],
                "stock_quantity": stock,
                "window_days": window_days,
                **forecast,
            }
        )

    items.sort(
        key=lambda it: (
            _STATUS_ORDER.get(it["status"], 9),
            it["days_remaining"] if it["days_remaining"] is not None else float("inf"),
        )
    )

    counts = {"urgent": 0, "soon": 0, "ok": 0, "no_data": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1

    summary = {
        "total_materials": len(items),
        "urgent": counts["urgent"],
        "soon": counts["soon"],
        "ok": counts["ok"],
        "no_data": counts["no_data"],
        "reorder_recommended": counts["urgent"] + counts["soon"],
    }

    return {
        "params": {
            "window_days": window_days,
            "default_lead_time_days": DEFAULT_LEAD_TIME_DAYS,
            "default_reorder_cycle_days": DEFAULT_REORDER_CYCLE_DAYS,
        },
        "summary": summary,
        "items": items,
    }


def forecast_alert(
    connection: sqlite3.Connection,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = 5,
) -> dict[str, Any]:
    """발주 임박(urgent+soon) 자재만 추려 대시보드 알림용으로 압축.

    :func:`compute_forecast` 의 summary/items 를 재사용한다. items 는 이미
    urgent → soon → ok → no_data, days_remaining 오름차순으로 정렬되어 있으므로
    urgent/soon 필터 후 앞에서 ``limit`` 개를 취하면 가장 임박한 자재가 위에 온다.

    Design: docs/02-design/features/forecast-dashboard-alert.design.md §2
    """
    full = compute_forecast(connection, window_days=window_days)
    summary = full["summary"]
    reorder = [
        {
            "material_id": it["material_id"],
            "name": it["name"],
            "category": it["category"],
            "unit": it["unit"],
            "status": it["status"],
            "days_remaining": it["days_remaining"],
            "predicted_stockout_date": it["predicted_stockout_date"],
            "recommended_order_qty": it["recommended_order_qty"],
        }
        for it in full["items"]
        if it["status"] in ("urgent", "soon")
    ]
    return {
        "window_days": window_days,
        "reorder_recommended": summary["reorder_recommended"],
        "urgent": summary["urgent"],
        "soon": summary["soon"],
        "shown": min(limit, len(reorder)),
        "items": reorder[:limit],
    }


def set_forecast_params(
    connection: sqlite3.Connection,
    material_id: int,
    *,
    lead_time_days: float,
    reorder_cycle_days: float,
) -> None:
    if lead_time_days < 0 or reorder_cycle_days < 0:
        raise ValueError("리드타임과 커버리지는 0 이상이어야 합니다.")
    connection.execute(
        "UPDATE materials SET lead_time_days = ?, reorder_cycle_days = ? WHERE id = ?",
        (lead_time_days, reorder_cycle_days, material_id),
    )
