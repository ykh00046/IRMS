from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_access_level
from ..database import get_connection, row_to_dict


def _parse_range(from_: str | None, to_: str | None) -> tuple[str, str, str, str]:
    today = date.today()
    if from_ is None and to_ is None:
        to_date = today
        from_date = today - timedelta(days=6)
    else:
        try:
            from_date = date.fromisoformat(from_) if from_ else today - timedelta(days=6)
            to_date = date.fromisoformat(to_) if to_ else today
        except ValueError:
            raise HTTPException(status_code=400, detail="INVALID_DATE")
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="INVALID_RANGE")
    from_ts = f"{from_date.isoformat()} 00:00:00"
    to_ts = f"{to_date.isoformat()} 23:59:59"
    return from_date.isoformat(), to_date.isoformat(), from_ts, to_ts


def _range_dict(from_date: str, to_date: str) -> dict[str, str]:
    return {"from": from_date, "to": to_date}


def _daterange(from_date: str, to_date: str) -> list[str]:
    f = date.fromisoformat(from_date)
    t = date.fromisoformat(to_date)
    out: list[str] = []
    cur = f
    while cur <= t:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def build_router() -> APIRouter:
    router = APIRouter(
        prefix="/dashboard",
        dependencies=[Depends(require_access_level("manager"))],
    )

    @router.get("/summary")
    async def dashboard_summary(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date, from_ts, to_ts = _parse_range(from_, to)
        with get_connection() as connection:
            completed = connection.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM recipes
                WHERE status = 'completed'
                  AND completed_at IS NOT NULL
                  AND completed_at BETWEEN ? AND ?
                """,
                (from_ts, to_ts),
            ).fetchone()["cnt"]

            meas_row = connection.execute(
                """
                SELECT COUNT(*) AS cnt, COALESCE(SUM(value_weight), 0) AS total_weight
                FROM recipe_items
                WHERE measured_at IS NOT NULL
                  AND measured_at BETWEEN ? AND ?
                """,
                (from_ts, to_ts),
            ).fetchone()

            day_rows = connection.execute(
                """
                SELECT substr(measured_at, 1, 10) AS d,
                       COUNT(*) AS cnt,
                       MIN(measured_at) AS first_at,
                       MAX(measured_at) AS last_at
                FROM recipe_items
                WHERE measured_at IS NOT NULL
                  AND measured_at BETWEEN ? AND ?
                GROUP BY d
                """,
                (from_ts, to_ts),
            ).fetchall()

        total_active_hours = 0.0
        for row in day_rows:
            total_active_hours += _compute_active_hours(row["first_at"], row["last_at"], row["cnt"])

        throughput = (meas_row["cnt"] / total_active_hours) if total_active_hours > 0 else 0.0

        return {
            "range": _range_dict(from_date, to_date),
            "completed_recipe_count": int(completed or 0),
            "measurement_count": int(meas_row["cnt"] or 0),
            "total_weight_g": round(float(meas_row["total_weight"] or 0.0), 2),
            "throughput_per_hour": round(throughput, 2),
        }

    @router.get("/materials")
    async def dashboard_materials(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=100),
    ) -> dict[str, Any]:
        from_date, to_date, from_ts, to_ts = _parse_range(from_, to)
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT m.id AS material_id,
                       m.name AS material_name,
                       m.category AS category,
                       COALESCE(SUM(ri.value_weight), 0) AS total_weight,
                       COUNT(*) AS cnt
                FROM recipe_items ri
                JOIN materials m ON m.id = ri.material_id
                WHERE ri.measured_at IS NOT NULL
                  AND ri.measured_at BETWEEN ? AND ?
                GROUP BY m.id, m.name, m.category
                ORDER BY total_weight DESC
                LIMIT ?
                """,
                (from_ts, to_ts, limit),
            ).fetchall()

        items = [
            {
                "material_id": int(r["material_id"]),
                "material_name": r["material_name"],
                "category": r["category"] or "",
                "total_weight_g": round(float(r["total_weight"] or 0.0), 2),
                "measurement_count": int(r["cnt"]),
            }
            for r in rows
        ]
        return {"range": _range_dict(from_date, to_date), "items": items}

    @router.get("/materials/{material_id}/recipes")
    async def dashboard_material_recipes(
        material_id: int,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date, from_ts, to_ts = _parse_range(from_, to)
        with get_connection() as connection:
            mat_row = connection.execute(
                "SELECT id, name FROM materials WHERE id = ?", (material_id,)
            ).fetchone()
            if not mat_row:
                raise HTTPException(status_code=404, detail="MATERIAL_NOT_FOUND")
            rows = connection.execute(
                """
                SELECT r.id AS recipe_id,
                       r.product_name,
                       r.ink_name,
                       ri.value_weight,
                       ri.measured_at,
                       ri.measured_by
                FROM recipe_items ri
                JOIN recipes r ON r.id = ri.recipe_id
                WHERE ri.material_id = ?
                  AND ri.measured_at IS NOT NULL
                  AND ri.measured_at BETWEEN ? AND ?
                ORDER BY ri.measured_at DESC
                LIMIT 200
                """,
                (material_id, from_ts, to_ts),
            ).fetchall()

        recipes = [
            {
                "recipe_id": int(r["recipe_id"]),
                "product_name": r["product_name"],
                "ink_name": r["ink_name"],
                "weight_g": round(float(r["value_weight"] or 0.0), 2),
                "measured_at": r["measured_at"],
                "measured_by": r["measured_by"] or "(미기록)",
            }
            for r in rows
        ]
        return {
            "range": _range_dict(from_date, to_date),
            "material_id": int(mat_row["id"]),
            "material_name": mat_row["name"],
            "recipes": recipes,
        }

    @router.get("/throughput")
    async def dashboard_throughput(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date, from_ts, to_ts = _parse_range(from_, to)
        with get_connection() as connection:
            day_rows = connection.execute(
                """
                SELECT substr(measured_at, 1, 10) AS d,
                       COUNT(*) AS cnt,
                       MIN(measured_at) AS first_at,
                       MAX(measured_at) AS last_at
                FROM recipe_items
                WHERE measured_at IS NOT NULL
                  AND measured_at BETWEEN ? AND ?
                GROUP BY d
                ORDER BY d ASC
                """,
                (from_ts, to_ts),
            ).fetchall()

        by_day_map = {
            r["d"]: {
                "cnt": int(r["cnt"]),
                "first_at": r["first_at"],
                "last_at": r["last_at"],
            }
            for r in day_rows
        }

        by_day: list[dict[str, Any]] = []
        total_measurements = 0
        total_active_hours = 0.0
        for d in _daterange(from_date, to_date):
            entry = by_day_map.get(d)
            if entry:
                hours = _compute_active_hours(entry["first_at"], entry["last_at"], entry["cnt"])
                thr = (entry["cnt"] / hours) if hours > 0 else 0.0
                total_measurements += entry["cnt"]
                total_active_hours += hours
                by_day.append(
                    {
                        "date": d,
                        "measurement_count": entry["cnt"],
                        "active_hours": round(hours, 2),
                        "throughput_per_hour": round(thr, 2),
                    }
                )
            else:
                by_day.append(
                    {
                        "date": d,
                        "measurement_count": 0,
                        "active_hours": 0.0,
                        "throughput_per_hour": 0.0,
                    }
                )

        overall = (total_measurements / total_active_hours) if total_active_hours > 0 else 0.0
        return {
            "range": _range_dict(from_date, to_date),
            "total_measurements": total_measurements,
            "total_active_hours": round(total_active_hours, 2),
            "throughput_per_hour": round(overall, 2),
            "by_day": by_day,
        }

    @router.get("/trend")
    async def dashboard_trend(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date, from_ts, to_ts = _parse_range(from_, to)
        with get_connection() as connection:
            completed_rows = connection.execute(
                """
                SELECT substr(completed_at, 1, 10) AS d, COUNT(*) AS cnt
                FROM recipes
                WHERE status = 'completed'
                  AND completed_at IS NOT NULL
                  AND completed_at BETWEEN ? AND ?
                GROUP BY d
                """,
                (from_ts, to_ts),
            ).fetchall()
            weight_rows = connection.execute(
                """
                SELECT substr(measured_at, 1, 10) AS d,
                       COALESCE(SUM(value_weight), 0) AS total_weight
                FROM recipe_items
                WHERE measured_at IS NOT NULL
                  AND measured_at BETWEEN ? AND ?
                GROUP BY d
                """,
                (from_ts, to_ts),
            ).fetchall()

        completed_map = {r["d"]: int(r["cnt"]) for r in completed_rows}
        weight_map = {r["d"]: float(r["total_weight"] or 0.0) for r in weight_rows}
        points = [
            {
                "date": d,
                "completed_count": completed_map.get(d, 0),
                "total_weight_g": round(weight_map.get(d, 0.0), 2),
            }
            for d in _daterange(from_date, to_date)
        ]
        return {"range": _range_dict(from_date, to_date), "points": points}

    @router.get("/operators")
    async def dashboard_operators(
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from_date, to_date, from_ts, to_ts = _parse_range(from_, to)
        with get_connection() as connection:
            meas_rows = connection.execute(
                """
                SELECT COALESCE(NULLIF(measured_by, ''), '(미기록)') AS operator,
                       COUNT(*) AS cnt,
                       COALESCE(SUM(value_weight), 0) AS total_weight
                FROM recipe_items
                WHERE measured_at IS NOT NULL
                  AND measured_at BETWEEN ? AND ?
                GROUP BY operator
                ORDER BY cnt DESC
                """,
                (from_ts, to_ts),
            ).fetchall()

            completed_rows = connection.execute(
                """
                SELECT COALESCE(NULLIF(created_by, ''), '(미기록)') AS operator,
                       COUNT(*) AS cnt
                FROM recipes
                WHERE status = 'completed'
                  AND completed_at IS NOT NULL
                  AND completed_at BETWEEN ? AND ?
                GROUP BY operator
                """,
                (from_ts, to_ts),
            ).fetchall()

        completed_map = {r["operator"]: int(r["cnt"]) for r in completed_rows}
        items = [
            {
                "operator": r["operator"],
                "measurement_count": int(r["cnt"]),
                "total_weight_g": round(float(r["total_weight"] or 0.0), 2),
                "completed_recipe_count": completed_map.get(r["operator"], 0),
            }
            for r in meas_rows
        ]
        return {"range": _range_dict(from_date, to_date), "items": items}

    return router


def _compute_active_hours(first_at: str | None, last_at: str | None, cnt: int) -> float:
    if not first_at or not last_at or cnt <= 0:
        return 0.0
    try:
        start = datetime.fromisoformat(first_at.replace("Z", ""))
        end = datetime.fromisoformat(last_at.replace("Z", ""))
    except ValueError:
        return max(cnt / 60.0, 0.0)
    elapsed = (end - start).total_seconds() / 3600.0
    floor = cnt / 60.0
    return max(elapsed, floor)
