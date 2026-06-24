"""합성 점도 등록·추세·이상 분석 서비스.

제품군마다 정상 점도 대역이 완전히 다르므로(PB~49, SBCT~204, SCRA~90) 모든
판정 기준은 제품(viscosity_products) 단위로 계산한다. 이상 판정은 두 축을
결합한다.

1. 관리 상/하한(spec)  : 관리자가 제품에 직접 설정한 lower_limit / upper_limit.
2. 통계 관리한계(sigma): 중심선 ± k·σ. 중심선은 target 이 있으면 target,
   없으면 표본 평균. σ 는 표본표준편차.

추세 룰(Western Electric 부분 집합)도 함께 본다.
- run: 연속 N회 단조 상승/하락
- shift: 중심선 한쪽으로 연속 M회 치우침

Plan:   docs/01-plan/features/viscosity-analysis.plan.md
Design: docs/02-design/features/viscosity-analysis.design.md
"""

import sqlite3
import statistics
from datetime import date, datetime
from typing import Any

# 추세 룰 파라미터
RUN_LENGTH = 5  # 연속 단조 상승/하락 N회 → 추세 경보
SHIFT_LENGTH = 7  # 중심선 한쪽 연속 M회 → 시프트 경보
WARN_SIGMA = 2.0  # 경고 구간: 2σ 초과 ~ kσ 이하


def parse_lot_date(lot_no: Any) -> str | None:
    """LOT 식별자에서 측정일(ISO date) 추론.

    - 8자리 YYMMDDSS (PB, 하루 2로트) → 20YY-MM-DD
    - 6자리 YYMMDD   (SBCT)            → 20YY-MM-DD
    - datetime / 'YYYY-MM-DD ...'      → 해당 날짜
    추론 불가 시 None.
    """
    if lot_no is None:
        return None
    if isinstance(lot_no, datetime):
        return lot_no.date().isoformat()
    if isinstance(lot_no, date):
        return lot_no.isoformat()

    text = str(lot_no).strip()
    # 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:MM:SS' 형태
    if "-" in text:
        try:
            return datetime.fromisoformat(text).date().isoformat()
        except ValueError:
            head = text.split(" ", 1)[0]
            try:
                return date.fromisoformat(head).isoformat()
            except ValueError:
                return None

    digits = text
    if not digits.isdigit():
        return None
    if len(digits) in (6, 8):
        yy, mm, dd = digits[0:2], digits[2:4], digits[4:6]
        try:
            year = 2000 + int(yy)
            return date(year, int(mm), int(dd)).isoformat()
        except ValueError:
            return None
    return None


def list_products(connection: sqlite3.Connection, *, active_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE is_active = 1" if active_only else ""
    rows = connection.execute(
        f"""
        SELECT id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, is_active, created_at
        FROM viscosity_products
        {where}
        ORDER BY is_active DESC, code ASC
        """
    ).fetchall()
    return [_serialize_product(row) for row in rows]


def get_product(connection: sqlite3.Connection, product_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, is_active, created_at
        FROM viscosity_products
        WHERE id = ?
        """,
        (product_id,),
    ).fetchone()
    return _serialize_product(row) if row else None


def get_product_by_code(connection: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, is_active, created_at
        FROM viscosity_products
        WHERE code = ?
        """,
        (code,),
    ).fetchone()
    return _serialize_product(row) if row else None


def _serialize_product(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "code": row["code"],
        "name": row["name"],
        "target": _opt_float(row["target"]),
        "lower_limit": _opt_float(row["lower_limit"]),
        "upper_limit": _opt_float(row["upper_limit"]),
        "sigma_k": float(row["sigma_k"]),
        "rpm": _opt_float(row["rpm"]),
        "temperature": _opt_float(row["temperature"]),
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "has_spec": row["lower_limit"] is not None or row["upper_limit"] is not None,
    }


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _fetch_readings(
    connection: sqlite3.Connection, product_id: int, year: int | None = None
) -> list[sqlite3.Row]:
    params: list[Any] = [product_id]
    year_clause = ""
    if year is not None:
        # measured_date 는 'YYYY-MM-DD' (또는 NULL). 연도 필터 시 날짜 없는 측정은 제외.
        year_clause = "AND substr(measured_date, 1, 4) = ?"
        params.append(f"{year:04d}")
    return connection.execute(
        f"""
        SELECT id, product_id, lot_no, viscosity, measured_date,
               memo, recipe_material, material_lot, created_by, created_at
        FROM viscosity_readings
        WHERE product_id = ? {year_clause}
        ORDER BY
            CASE WHEN measured_date IS NULL THEN 1 ELSE 0 END,
            measured_date ASC,
            lot_no ASC,
            id ASC
        """,
        params,
    ).fetchall()


def available_years(connection: sqlite3.Connection, product_id: int) -> list[int]:
    """제품에 측정 기록이 있는 연도 목록 (내림차순)."""
    rows = connection.execute(
        """
        SELECT DISTINCT substr(measured_date, 1, 4) AS y
        FROM viscosity_readings
        WHERE product_id = ? AND measured_date IS NOT NULL
        ORDER BY y DESC
        """,
        (product_id,),
    ).fetchall()
    return [int(r["y"]) for r in rows if r["y"] and str(r["y"]).isdigit()]


def _control_limits(product: dict[str, Any], values: list[float]) -> dict[str, Any]:
    """제품 설정 + 표본으로부터 중심선/통계 관리한계를 산출."""
    n = len(values)
    mean = statistics.fmean(values) if n else None
    std = statistics.stdev(values) if n >= 2 else 0.0
    center = product["target"] if product["target"] is not None else mean
    sigma_k = product["sigma_k"]

    ucl = lcl = uwl = lwl = None
    if center is not None and std > 0:
        ucl = center + sigma_k * std
        lcl = center - sigma_k * std
        uwl = center + WARN_SIGMA * std
        lwl = center - WARN_SIGMA * std

    return {
        "n": n,
        "mean": round(mean, 3) if mean is not None else None,
        "std": round(std, 3),
        "min": round(min(values), 3) if values else None,
        "max": round(max(values), 3) if values else None,
        "center": round(center, 3) if center is not None else None,
        "sigma_k": sigma_k,
        "ucl": round(ucl, 3) if ucl is not None else None,
        "lcl": round(lcl, 3) if lcl is not None else None,
        "uwl": round(uwl, 3) if uwl is not None else None,
        "lwl": round(lwl, 3) if lwl is not None else None,
    }


def _classify(value: float, product: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    """단일 측정값의 이상 여부 판정 (spec + sigma 결합)."""
    reasons: list[str] = []
    side = None  # 'high' / 'low'

    upper = product["upper_limit"]
    lower = product["lower_limit"]
    if upper is not None and value > upper:
        reasons.append("spec_high")
        side = "high"
    if lower is not None and value < lower:
        reasons.append("spec_low")
        side = "low"

    ucl, lcl = control["ucl"], control["lcl"]
    if ucl is not None and value > ucl:
        reasons.append("sigma_high")
        side = side or "high"
    if lcl is not None and value < lcl:
        reasons.append("sigma_low")
        side = side or "low"

    if reasons:
        return {"status": "anomaly", "side": side, "reasons": reasons}

    # 경고 구간 (2σ 초과 ~ kσ 이하)
    uwl, lwl = control["uwl"], control["lwl"]
    if uwl is not None and value > uwl:
        return {"status": "warn", "side": "high", "reasons": ["warn_high"]}
    if lwl is not None and value < lwl:
        return {"status": "warn", "side": "low", "reasons": ["warn_low"]}

    return {"status": "normal", "side": None, "reasons": []}


def _trend_alerts(values: list[float], center: float | None) -> list[dict[str, Any]]:
    """말단 구간의 추세(run / shift) 경보를 산출."""
    alerts: list[dict[str, Any]] = []
    n = len(values)

    # run: 끝에서부터 연속 단조 상승/하락 길이
    if n >= RUN_LENGTH:
        up = down = 1
        for i in range(n - 1, 0, -1):
            if values[i] > values[i - 1]:
                up += 1
                if down > 1:
                    break
            else:
                break
        for i in range(n - 1, 0, -1):
            if values[i] < values[i - 1]:
                down += 1
                if up > 1:
                    break
            else:
                break
        if up >= RUN_LENGTH:
            alerts.append({"type": "run_up", "length": up})
        elif down >= RUN_LENGTH:
            alerts.append({"type": "run_down", "length": down})

    # shift: 중심선 한쪽으로 연속 치우침
    if center is not None and n >= SHIFT_LENGTH:
        above = below = 0
        for v in reversed(values):
            if v > center:
                if below:
                    break
                above += 1
            elif v < center:
                if above:
                    break
                below += 1
            else:
                break
        if above >= SHIFT_LENGTH:
            alerts.append({"type": "shift_high", "length": above})
        elif below >= SHIFT_LENGTH:
            alerts.append({"type": "shift_low", "length": below})

    return alerts


def _period_key(date_str: str | None, granularity: str) -> str | None:
    """측정일(ISO date)에서 기간 버킷 키 생성. 'quarter' → '2026-Q1', 'month' → '2026-03'."""
    if not date_str:
        return None
    try:
        year = int(date_str[0:4])
        month = int(date_str[5:7])
    except (ValueError, IndexError):
        return None
    if not 1 <= month <= 12:
        return None
    if granularity == "year":
        return f"{year:04d}"
    if granularity == "month":
        return f"{year:04d}-{month:02d}"
    return f"{year}-Q{(month - 1) // 3 + 1}"


def summarize_periods(readings: list[dict[str, Any]], granularity: str) -> list[dict[str, Any]]:
    """측정 시계열을 기간(분기/월)으로 묶어 건수·평균·σ·범위·이상수 + 전기대비 평균변화."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in readings:
        key = _period_key(r["measured_date"], granularity)
        if key is None:
            continue
        buckets.setdefault(key, []).append(r)

    result: list[dict[str, Any]] = []
    prev_mean: float | None = None
    for key in sorted(buckets):
        items = buckets[key]
        values = [x["viscosity"] for x in items]
        mean = round(statistics.fmean(values), 3)
        std = round(statistics.stdev(values), 3) if len(values) >= 2 else 0.0
        delta = None if prev_mean is None else round(mean - prev_mean, 3)
        result.append({
            "period": key,
            "count": len(values),
            "mean": mean,
            "std": std,
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "anomaly_count": sum(1 for x in items if x["status"] == "anomaly"),
            "warn_count": sum(1 for x in items if x["status"] == "warn"),
            "mean_delta": delta,
        })
        prev_mean = mean
    return result


def _period_alerts(periods: list[dict[str, Any]], control_std: float) -> list[dict[str, Any]]:
    """기간 집계에서 이상 급증 / 평균 이동(드리프트) 경보를 추출.

    - anomaly_spike: 직전 기간 대비 이상 건수가 2건 이상으로 늘어난 기간
    - mean_shift   : 전기대비 평균변화가 전체 σ 이상인 기간(공정 평균 드리프트)
    """
    alerts: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for p in periods:
        if prev is not None:
            if p["anomaly_count"] >= 2 and p["anomaly_count"] > prev["anomaly_count"]:
                alerts.append({
                    "period": p["period"],
                    "type": "anomaly_spike",
                    "anomaly_count": p["anomaly_count"],
                    "prev_count": prev["anomaly_count"],
                })
            if (
                control_std > 0
                and p["mean_delta"] is not None
                and abs(p["mean_delta"]) >= control_std
            ):
                alerts.append({
                    "period": p["period"],
                    "type": "mean_shift_up" if p["mean_delta"] > 0 else "mean_shift_down",
                    "delta": p["mean_delta"],
                })
        prev = p
    return alerts


def classify_value(
    connection: sqlite3.Connection,
    product: dict[str, Any],
    value: float,
    year: int | None = None,
) -> dict[str, Any]:
    """단일 값을 현재 제품 기준으로 판정 (신규 입력 즉시 경고용).

    중심선/관리한계는 같은 연도의 기존 측정 표본 + 제품 설정으로 산출한다
    (입력값 포함 전 기준). year 미지정 시 전체 표본.
    """
    rows = _fetch_readings(connection, product["id"], year)
    values = [float(r["viscosity"]) for r in rows]
    control = _control_limits(product, values)
    verdict = _classify(value, product, control)
    verdict["control"] = control
    return verdict


def analyze_product(
    connection: sqlite3.Connection,
    product: dict[str, Any],
    *,
    granularity: str = "quarter",
    year: int | None = None,
) -> dict[str, Any]:
    """제품 단위 분석: 통계 + 관리한계 + 측정 시계열(이상 표기) + 이상/추세 + 기간 집계.

    year 지정 시 해당 연도 표본만으로 기준(중심선/σ/이상)을 계산한다. 같은 제품이라도
    연도/공정에 따라 점도 대역이 달라지므로 연도별 기준이 기본 분석 단위.
    """
    rows = _fetch_readings(connection, product["id"], year)
    values = [float(r["viscosity"]) for r in rows]
    control = _control_limits(product, values)

    readings: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for r in rows:
        value = float(r["viscosity"])
        verdict = _classify(value, product, control)
        item = {
            "id": int(r["id"]),
            "lot_no": r["lot_no"],
            "viscosity": value,
            "measured_date": r["measured_date"],
            "memo": r["memo"],
            "recipe_material": r["recipe_material"],
            "material_lot": r["material_lot"],
            "created_by": r["created_by"],
            "status": verdict["status"],
            "side": verdict["side"],
            "reasons": verdict["reasons"],
        }
        readings.append(item)
        if verdict["status"] == "anomaly":
            anomalies.append(item)

    trends = _trend_alerts(values, control["center"])
    counts = {
        "anomaly": sum(1 for x in readings if x["status"] == "anomaly"),
        "warn": sum(1 for x in readings if x["status"] == "warn"),
        "normal": sum(1 for x in readings if x["status"] == "normal"),
    }
    periods = summarize_periods(readings, granularity)

    return {
        "product": product,
        "stats": control,
        "counts": counts,
        "readings": readings,
        "anomalies": list(reversed(anomalies)),  # 최신 이상 먼저
        "trends": trends,
        "granularity": granularity,
        "year": year,
        "available_years": available_years(connection, product["id"]),
        "periods": periods,
        "period_alerts": _period_alerts(periods, control["std"]),
    }


def overview(connection: sqlite3.Connection) -> dict[str, Any]:
    """전 제품 요약: 제품별 '최신 연도' 기준 최근값/평균/이상 건수/마지막 상태.

    제품마다 연도별로 점도 대역이 다르므로, 전 연도를 한데 섞으면 평균·σ·이상수가
    왜곡된다. 따라서 카드 요약은 각 제품의 가장 최근 연도 표본으로 계산한다.
    """
    products = list_products(connection)
    items: list[dict[str, Any]] = []
    total_anomaly = 0
    for product in products:
        years = available_years(connection, product["id"])
        latest_year = years[0] if years else None
        analysis = analyze_product(connection, product, year=latest_year)
        readings = analysis["readings"]
        last = readings[-1] if readings else None
        anomaly_count = analysis["counts"]["anomaly"]
        total_anomaly += anomaly_count
        items.append({
            "id": product["id"],
            "code": product["code"],
            "name": product["name"],
            "is_active": product["is_active"],
            "has_spec": product["has_spec"],
            "year": latest_year,
            "count": analysis["stats"]["n"],
            "mean": analysis["stats"]["mean"],
            "std": analysis["stats"]["std"],
            "latest_value": last["viscosity"] if last else None,
            "latest_date": last["measured_date"] if last else None,
            "last_status": last["status"] if last else None,
            "anomaly_count": anomaly_count,
            "warn_count": analysis["counts"]["warn"],
            "trend_count": len(analysis["trends"]),
        })
    return {
        "items": items,
        "total_anomaly": total_anomaly,
        "product_count": len(items),
    }


def add_reading(
    connection: sqlite3.Connection,
    *,
    product_id: int,
    lot_no: str,
    viscosity: float,
    measured_date: str | None,
    memo: str | None,
    recipe_material: str | None,
    material_lot: str | None,
    created_by: str | None,
    created_at: str,
    blend_record_id: int | None = None,
) -> int:
    """점도 측정 1건 등록. measured_date 미지정 시 LOT 에서 추론, 실패 시 등록일.

    blend_record_id 지정 시 해당 배합 실적과 연계된다([[blend-overhaul]]).
    """
    resolved_date = measured_date or parse_lot_date(lot_no) or created_at[:10]
    cur = connection.execute(
        """
        INSERT INTO viscosity_readings
            (product_id, lot_no, viscosity, measured_date, memo,
             recipe_material, material_lot, created_by, created_at, blend_record_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            lot_no.strip(),
            viscosity,
            resolved_date,
            (memo or "").strip() or None,
            (recipe_material or "").strip() or None,
            (material_lot or "").strip() or None,
            created_by,
            created_at,
            blend_record_id,
        ),
    )
    return int(cur.lastrowid)


def list_readings_for_blend(
    connection: sqlite3.Connection, blend_record_id: int
) -> list[dict[str, Any]]:
    """배합 실적에 연계된 점도 측정 목록 (제품 코드 포함)."""
    rows = connection.execute(
        """
        SELECT r.id, r.viscosity, r.measured_date, r.memo, r.lot_no, r.created_by,
               p.code AS product_code, p.name AS product_name, p.id AS product_id
        FROM viscosity_readings r
        JOIN viscosity_products p ON p.id = r.product_id
        WHERE r.blend_record_id = ?
        ORDER BY r.id DESC
        """,
        (blend_record_id,),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "viscosity": float(r["viscosity"]),
            "measured_date": r["measured_date"],
            "memo": r["memo"],
            "lot_no": r["lot_no"],
            "product_id": int(r["product_id"]),
            "product_code": r["product_code"],
            "product_name": r["product_name"],
            "created_by": r["created_by"],
        }
        for r in rows
    ]
