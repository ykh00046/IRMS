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

from . import settings_service

# 추세 룰 파라미터
RUN_LENGTH = 5  # 연속 단조 상승/하락 N회 → 추세 경보
SHIFT_LENGTH = 7  # 중심선 한쪽 연속 M회 → 시프트 경보
WARN_SIGMA = 2.0  # 경고 구간: 2σ 초과 ~ kσ 이하
# 통계 관리한계(σ 기반)를 신뢰하려면 필요한 최소 표본 수.
# 표본이 3~5개뿐일 때 stdev 는 우연히 아주 작게 나온다(예: 49.0/49.0/49.1 → σ=0.058,
# UCL 49.207). 그러면 49.4 같은 정상 산포가 '이상'으로 뜨고, 현장은 판정 자체를
# 불신하게 된다. 반대로 n=2 는 한계가 터무니없이 넓어 진짜 이상도 통과한다.
# 표본이 이 수에 못 미치면 σ 판정은 보류하고 **규격(spec) 판정만** 적용한다
# — 규격은 표본과 무관한 공학 기준이라 언제나 유효하다.
MIN_SIGMA_SAMPLES = 8


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
        SELECT id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, remind_daily, use_reactor, is_active, created_at
        FROM viscosity_products
        {where}
        ORDER BY is_active DESC, code ASC
        """
    ).fetchall()
    return [_serialize_product(connection, row) for row in rows]


def get_product(connection: sqlite3.Connection, product_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, remind_daily, use_reactor, is_active, created_at
        FROM viscosity_products
        WHERE id = ?
        """,
        (product_id,),
    ).fetchone()
    return _serialize_product(connection, row) if row else None


def get_product_by_code(connection: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    # GAP-4: 코드 비교는 strip+upper 정규화(대소문자·앞뒤 공백 무시)로 조회한다.
    # 리마인더 쿼리(daily_reading_reminders 의 upper(p.code))·자동 생성(ensure_product_by_code)이
    # 모두 같은 정규화를 쓰므로, product_name 이 대소문자/공백만 달라도 같은 논리적 제품으로
    # 귀결돼 중복 점도 제품이 생기지 않는다.
    normalized = str(code or "").strip().upper()
    row = connection.execute(
        """
        SELECT id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, remind_daily, use_reactor, is_active, created_at
        FROM viscosity_products
        WHERE upper(code) = ?
        """,
        (normalized,),
    ).fetchone()
    return _serialize_product(connection, row) if row else None


def ensure_product_by_code(
    connection: sqlite3.Connection, code: str, name: str | None, created_at: str
) -> dict[str, Any] | None:
    """제품 코드로 점도 제품을 찾고, 없으면 생성(spec 미설정)해서 반환.

    배합 기록에서 점도를 등록할 때 그 제품(레시피)명으로 점도 제품을 자동 확보한다.
    추세/관리한계 spec(target/limit)은 관리자가 점도 설정에서 따로 채울 수 있다.
    """
    code = str(code or "").strip()
    if not code:
        return None
    existing = get_product_by_code(connection, code)
    if existing:
        return existing
    cur = connection.execute(
        "INSERT INTO viscosity_products (code, name, is_active, created_at) VALUES (?, ?, 1, ?)",
        (code, (name or code).strip(), created_at),
    )
    return get_product(connection, int(cur.lastrowid))


def _recipe_use_reactor(connection: sqlite3.Connection, code: Any, name: Any) -> bool | None:
    """반응기 사용 여부를 레시피에서 우선 조회 — code 또는 name 에 매칭되는 최신 completed
    레시피(recipes.use_reactor)가 있으면 그 값을, 없으면 None(폴백 필요)을 반환.

    소유가 recipes 로 이전되어 점도 제품 행의 use_reactor 열은 레거시 폴백 용도로만 쓰인다.
    recipes 테이블이 없는 단위 테스트 스키마에서는 폴백(None)으로 간주한다.
    """
    candidates = [v for v in (code, name) if v not in (None, "")]
    if not candidates:
        return None
    placeholders = " OR ".join("product_name = ?" for _ in candidates)
    try:
        row = connection.execute(
            f"SELECT use_reactor FROM recipes "
            f"WHERE ({placeholders}) AND status = 'completed' "
            f"ORDER BY id DESC LIMIT 1",
            candidates,
        ).fetchone()
    except sqlite3.OperationalError:
        # recipes 테이블이 없는 스키마(단위 테스트) — 폴백.
        return None
    return bool(row["use_reactor"]) if row else None


def _recipe_category(connection: sqlite3.Connection, code: Any, name: Any) -> str | None:
    """반제품의 분류(약품/합성/잉크/용수)를 레시피에서 가져온다.

    viscosity_products 에는 분류 열이 없고, 분류는 레시피 관리에서 지정한다
    (recipes.category). 여기에 열을 하나 더 만들면 같은 사실을 두 곳에서 관리하게 되고
    관리 화면에서 분류를 바꿔도 점도 화면이 옛 값을 계속 보여준다 — 원본에서 읽는다.
    use_reactor 와 같은 매칭 규칙(code 또는 name = product_name, 최신 completed).
    """
    candidates = [v for v in (code, name) if v not in (None, "")]
    if not candidates:
        return None
    placeholders = " OR ".join("product_name = ?" for _ in candidates)
    try:
        row = connection.execute(
            f"SELECT category FROM recipes "
            f"WHERE ({placeholders}) AND status = 'completed' "
            f"ORDER BY id DESC LIMIT 1",
            candidates,
        ).fetchone()
    except sqlite3.OperationalError:
        # recipes 테이블/열이 없는 스키마(단위 테스트) — 분류 없음으로 본다.
        return None
    return (row["category"] or None) if row else None


def _serialize_product(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    # use_reactor 소유 이전: 매칭되는 최신 completed 레시피 값이 우선, 없으면 구 열(폴백).
    recipe_use = _recipe_use_reactor(connection, row["code"], row["name"])
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
        "remind_daily": bool(row["remind_daily"]),
        "use_reactor": bool(row["use_reactor"]) if recipe_use is None else recipe_use,
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "has_spec": row["lower_limit"] is not None or row["upper_limit"] is not None,
        "category": _recipe_category(connection, row["code"], row["name"]),
    }


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _is_excluded(row: Any) -> bool:
    """측정 행이 '통계 제외'로 표시됐는지. 스키마에 excluded 열이 없거나(구 테스트
    스키마) NULL 이면 제외 아님으로 본다."""
    try:
        return bool(row["excluded"])
    except (KeyError, IndexError):
        return False


def _fetch_readings(
    connection: sqlite3.Connection,
    product_id: int,
    year: int | None = None,
    reactor: int | None = None,
) -> list[sqlite3.Row]:
    params: list[Any] = [product_id]
    year_clause = ""
    if year is not None:
        # measured_date 는 'YYYY-MM-DD' (또는 NULL). 연도 필터 시 날짜 없는 측정은 제외.
        year_clause = "AND substr(measured_date, 1, 4) = ?"
        params.append(f"{year:04d}")
    reactor_clause = ""
    if reactor == "none":
        # 미지정(과거 데이터 등) 전용 뷰 — 반응기 도입 전 기록을 명시적으로 본다.
        reactor_clause = "AND reactor IS NULL"
    elif reactor is not None:
        reactor_clause = "AND reactor = ?"
        params.append(int(reactor))
    return connection.execute(
        f"""
        SELECT id, product_id, lot_no, viscosity, measured_date,
               memo, recipe_material, material_lot, reactor, created_by, created_at,
               excluded, exclude_reason, excluded_by, excluded_at
        FROM viscosity_readings
        WHERE product_id = ? {year_clause} {reactor_clause}
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


def available_reactors(connection: sqlite3.Connection, product_id: int) -> list[int]:
    """제품에 측정 기록이 있는 반응기 번호 목록 (오름차순)."""
    rows = connection.execute(
        """
        SELECT DISTINCT reactor
        FROM viscosity_readings
        WHERE product_id = ? AND reactor IS NOT NULL
        ORDER BY reactor ASC
        """,
        (product_id,),
    ).fetchall()
    return [int(r["reactor"]) for r in rows if r["reactor"] is not None]


def _control_limits(product: dict[str, Any], values: list[float]) -> dict[str, Any]:
    """제품 설정 + 표본으로부터 중심선/통계 관리한계를 산출."""
    n = len(values)
    mean = statistics.fmean(values) if n else None
    std = statistics.stdev(values) if n >= 2 else 0.0
    center = product["target"] if product["target"] is not None else mean
    sigma_k = product["sigma_k"]

    ucl = lcl = uwl = lwl = None
    sigma_ready = n >= MIN_SIGMA_SAMPLES
    if center is not None and std > 0 and sigma_ready:
        ucl = center + sigma_k * std
        lcl = center - sigma_k * std
        # 경고 밴드 붕괴 방지(POLISH-2): sigma_k <= WARN_SIGMA 면 kσ(UCL) 가 2σ(UWL)
        # 안쪽에 놓여, 2σ 초과 값이 warn 이 아니라 곧바로 anomaly 로 걸려 '경고' 가
        # 실질적으로 사라진다(_classify 는 anomaly 를 먼저 반환). 이 경우 경고 밴드를
        # 아예 없애(None) 두어 '경고 > 이상' 역전을 원천 차단한다 — 경고 없이 정상↔이상만.
        if sigma_k > WARN_SIGMA:
            uwl = center + WARN_SIGMA * std
            lwl = center - WARN_SIGMA * std

    return {
        "n": n,
        # σ 판정 가능 여부 — False 면 관리한계가 없고 규격 판정만 적용된다(화면 안내용).
        "sigma_ready": sigma_ready,
        "sigma_min_samples": MIN_SIGMA_SAMPLES,
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

    # run: 끝에서부터 연속 단조 상승/하락 길이.
    # 두 방향을 대칭으로 독립 집계한다. 말단 구간은 한 방향으로만 단조일 수 있으므로
    # (마지막 스텝이 상승이면 하락 루프는 즉시 멈춰 down=1, 그 반대도 마찬가지, 동값이면
    # 둘 다 1) up/down 중 최대 하나만 RUN_LENGTH 이상이 된다.
    if n >= RUN_LENGTH:
        up = down = 1
        for i in range(n - 1, 0, -1):
            if values[i] > values[i - 1]:
                up += 1
            else:
                break
        for i in range(n - 1, 0, -1):
            if values[i] < values[i - 1]:
                down += 1
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
    """측정일(ISO date)에서 기간 버킷 키 생성.

    'day' → '2026-03-15', 'week' → '2026-W11'(ISO 주차), 'month' → '2026-03',
    'quarter' → '2026-Q1', 'year' → '2026'. 모든 키는 사전식 정렬=시간순 정렬.
    """
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
    if granularity in ("day", "week"):
        # 일/주는 측정일 전체가 필요 — 유효한 날짜인지 확인 후 버킷.
        try:
            day = int(date_str[8:10])
            d = date(year, month, day)
        except (ValueError, IndexError):
            return None
        if granularity == "day":
            return d.isoformat()                       # 2026-03-15
        iso = d.isocalendar()                          # (ISO년, ISO주차, 요일)
        return f"{iso[0]:04d}-W{iso[1]:02d}"           # 2026-W11
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


def _period_alerts(
    periods: list[dict[str, Any]], control_std: float, granularity: str = "month"
) -> list[dict[str, Any]]:
    """기간 집계에서 이상 급증 / 평균 이동(드리프트) 경보를 추출.

    - anomaly_spike: 직전 기간 대비 이상 건수가 2건 이상으로 늘어난 기간.
      mean_shift 와 동일하게 월/분기/연도 단위에서만 계산 — 일/주 단위는 구간당
      측정이 1~2건이라 하루에 이상 2건만 몰려도 경보가 떠 과민했다(GAP-3). 완화
      게이트를 두 경보에 일관 적용한다.
    - mean_shift   : 전기대비 평균변화가 전체 σ 이상인 기간(공정 평균 드리프트).
      월/분기/연도 단위에서만 계산 — 일/주 단위는 구간이 측정 1~2건이라 평균이
      사실상 개별 측정값이고, 정상 등락(±1σ)이 전부 경보로 잡히는 과민 문제가
      있었다(2026-07-22 현장 보고: 46.8~49.8 정상 범위에서 경보 18건).
    """
    coarse = granularity in ("month", "quarter", "year")
    alerts: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for p in periods:
        if prev is not None:
            if (
                coarse
                and p["anomaly_count"] >= 2
                and p["anomaly_count"] > prev["anomaly_count"]
            ):
                alerts.append({
                    "period": p["period"],
                    "type": "anomaly_spike",
                    "anomaly_count": p["anomaly_count"],
                    "prev_count": prev["anomaly_count"],
                })
            if (
                coarse
                and control_std > 0
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
    reactor: int | None = None,
) -> dict[str, Any]:
    """단일 값을 현재 제품 기준으로 판정 (신규 입력 즉시 경고용).

    중심선/관리한계는 같은 연도(+반응기) 의 측정 표본 + 제품 설정으로 산출하며,
    **판정 대상 값도 표본에 포함한다** — 저장 후 목록(analyze_product)이 그 값을 포함해
    다시 계산하므로, 포함하지 않으면 같은 응답 안에서 new_reading.status(이상)와
    readings[] 의 같은 행(정상)이 서로 모순됐다. year 미지정 시 전체 표본.
    """
    rows = _fetch_readings(connection, product["id"], year, reactor)
    # 통계 자기 오염 방지: 이미 '통계 제외' 로 표시된 측정은 표본에서 뺀다(제외의 목적).
    values = [float(r["viscosity"]) for r in rows if not _is_excluded(r)]
    control = _control_limits(product, [*values, float(value)])
    verdict = _classify(value, product, control)
    verdict["control"] = control
    return verdict


def _lot_digits(lot: Any) -> str:
    """LOT 에서 뒤쪽 8자리 숫자만 추출 — 연계 매칭 키.

    PB 점도 LOT 은 저장 경로마다 형식이 다르다: 배합 화면 등록은 product_lot
    (예: PB26010701, 제품명 접두사 포함), 엑셀 임포트는 8자리(26010701). 바인더의
    사용한PB 는 접두사 없는 8자리다. 숫자만 뽑아 뒤 8자리로 맞추면 어느 형식이든
    같은 배합을 가리키면 매칭된다(제품명에 숫자가 없는 PB 라 안전).
    """
    digits = "".join(ch for ch in str(lot or "") if ch.isdigit())
    return digits[-8:] if len(digits) >= 8 else digits


def _pb_viscosity_map(connection: sqlite3.Connection) -> dict[str, float]:
    """PB 반제품의 {LOT 숫자(8자리) → 최신 점도} 맵. 바인더의 사용한PB 연계에 쓴다.

    같은 PB LOT 에 점도가 여러 번이면 가장 최근(measured_date, id) 것을 쓴다.
    PB 반제품이 없으면 빈 맵.
    """
    pb = get_product_by_code(connection, "PB")
    if not pb:
        return {}
    rows = connection.execute(
        "SELECT lot_no, viscosity FROM viscosity_readings WHERE product_id = ? "
        "AND excluded = 0 "
        "ORDER BY measured_date ASC, id ASC",
        (pb["id"],),
    ).fetchall()
    # ASC 로 돌며 덮어쓰면 마지막(=최신) 값이 남는다. 키는 숫자 8자리로 정규화.
    out: dict[str, float] = {}
    for r in rows:
        key = _lot_digits(r["lot_no"])
        if key:
            out[key] = float(r["viscosity"])
    return out


def analyze_product(
    connection: sqlite3.Connection,
    product: dict[str, Any],
    *,
    granularity: str = "quarter",
    year: int | None = None,
    reactor: int | None = None,
) -> dict[str, Any]:
    """제품 단위 분석: 통계 + 관리한계 + 측정 시계열(이상 표기) + 이상/추세 + 기간 집계.

    year 지정 시 해당 연도 표본만으로 기준(중심선/σ/이상)을 계산한다. 같은 제품이라도
    연도/공정에 따라 점도 대역이 달라지므로 연도별 기준이 기본 분석 단위. reactor 지정
    시 해당 반응기 표본만으로 계산(반응기별 추세).
    """
    rows = _fetch_readings(connection, product["id"], year, reactor)
    # 통계(평균/σ/관리한계/추세/기간)는 '통계 제외'되지 않은 유효 측정만으로 계산한다.
    # 이상 하나가 그 이상을 잡아야 할 σ 를 스스로 오염시키는 문제를 여기서 끊는다.
    # 제외된 측정은 readings[] 에는 그대로 남겨 화면에 배지+사유로 보여준다(단 판정·집계 제외).
    valid_values = [float(r["viscosity"]) for r in rows if not _is_excluded(r)]
    control = _control_limits(product, valid_values)

    # 사용한 PB 연계 — 바인더(APB/CSPB 등)의 material_lot(사용한PB) 을 PB 반제품의
    # 점도(lot_no) 와 맞춰, "이 PB(48cp)로 만든 바인더는 80" 상관을 보여준다. 두 LOT
    # 은 같은 8자리 형식이라 직접 매칭. PB 자신을 볼 때나 매칭이 없으면 그냥 빈 값.
    pb_map = _pb_viscosity_map(connection) if product.get("code") != "PB" else {}

    readings: list[dict[str, Any]] = []
    valid_readings: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    excluded_count = 0
    for r in rows:
        value = float(r["viscosity"])
        excluded = _is_excluded(r)
        source_lot = _lot_digits(r["material_lot"])
        item = {
            "id": int(r["id"]),
            "lot_no": r["lot_no"],
            "viscosity": value,
            "measured_date": r["measured_date"],
            "memo": r["memo"],
            "recipe_material": r["recipe_material"],
            "material_lot": r["material_lot"],
            "source_pb_viscosity": pb_map.get(source_lot),
            "reactor": r["reactor"],
            "created_by": r["created_by"],
            "excluded": excluded,
            "exclude_reason": r["exclude_reason"],
            "excluded_by": r["excluded_by"],
            "excluded_at": r["excluded_at"],
        }
        if excluded:
            # 제외된 측정은 spec/σ 판정을 건너뛰고 status='excluded' 로만 표시한다.
            excluded_count += 1
            item["status"] = "excluded"
            item["side"] = None
            item["reasons"] = []
            readings.append(item)
            continue
        verdict = _classify(value, product, control)
        item["status"] = verdict["status"]
        item["side"] = verdict["side"]
        item["reasons"] = verdict["reasons"]
        readings.append(item)
        valid_readings.append(item)
        if verdict["status"] == "anomaly":
            anomalies.append(item)

    trends = _trend_alerts(valid_values, control["center"])
    counts = {
        "anomaly": sum(1 for x in valid_readings if x["status"] == "anomaly"),
        "warn": sum(1 for x in valid_readings if x["status"] == "warn"),
        "normal": sum(1 for x in valid_readings if x["status"] == "normal"),
        "excluded": excluded_count,
    }
    # 기간 집계도 유효 측정만으로 — 제외된 이상이 기간 평균/σ 를 밀어올리지 않게 한다.
    periods = summarize_periods(valid_readings, granularity)
    control["excluded_n"] = excluded_count

    return {
        "product": product,
        "stats": control,
        "counts": counts,
        "readings": readings,
        "anomalies": list(reversed(anomalies)),  # 최신 이상 먼저
        "trends": trends,
        "granularity": granularity,
        "year": year,
        "reactor": reactor,
        "available_years": available_years(connection, product["id"]),
        "available_reactors": available_reactors(connection, product["id"]),
        "periods": periods,
        "period_alerts": _period_alerts(periods, control["std"], granularity),
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
            "category": product.get("category"),   # 반제품 선택 좁히기용(배합 화면과 동일 분류)
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


def daily_reading_reminders(
    connection: sqlite3.Connection,
    *,
    target_date: str,
    codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """오늘(target_date) 측정이 밀린 '매일 알림 대상'(remind_daily=1) 반제품 목록.

    알림 대상 여부는 웹 점도 설정이 소유한다(remind_daily 플래그). codes 는 선택적
    추가 필터일 뿐이며, 비어 있으면 알림 대상 전체를 대상으로 한다(서버 주도).

    정리 기준일(app_settings.viscosity_reminder_since)이 있으면, **그 날짜 이후에 실제로
    배합한 반제품만** 알린다. 지나간 배합은 이제 와서 점도를 잴 수 없는데도 대상 품목이면
    매일 팝업이 떠서(현장 요청 2026-08-07), 책임자가 [지금까지 정리] 를 누른 시점 이전은
    덮는다. 기준일이 없으면 종전대로 전 대상 품목을 본다.
    """
    normalized_codes: list[str] = []
    seen_codes: set[str] = set()
    for code in codes or []:
        normalized = str(code or "").strip().upper()
        if normalized and normalized not in seen_codes:
            normalized_codes.append(normalized)
            seen_codes.add(normalized)

    where = ["p.is_active = 1", "p.remind_daily = 1"]
    params: list[Any] = []
    if normalized_codes:
        placeholders = ",".join("?" for _code in normalized_codes)
        where.append(f"upper(p.code) IN ({placeholders})")
        params.extend(normalized_codes)
    # 정리 기준일 이후 배합이 있는 품목만 — 반제품명(product_name)으로 대조한다.
    # 점도 제품과 배합 레시피는 이름으로 이어져 있다(blend_add_viscosity 의 ensure_product
    # 규약과 동일). 이름이 안 맞는 옛 품목은 조용해지는데, 그건 이 기능의 의도다.
    since = settings_service.get_viscosity_reminder_since(connection)
    if since:
        where.append(
            "EXISTS (SELECT 1 FROM blend_records b"
            " WHERE b.status != 'canceled'"
            "   AND b.work_date >= ?"
            "   AND (b.product_name = p.name OR b.product_name = p.code))"
        )
        params.append(since)
    params.append(target_date)  # NOT EXISTS today.measured_date = ?

    rows = connection.execute(
        f"""
        SELECT
            p.id,
            p.code,
            p.name,
            latest.viscosity AS latest_value,
            latest.measured_date AS latest_date
        FROM viscosity_products p
        LEFT JOIN (
            SELECT r.product_id, r.viscosity, r.measured_date
            FROM viscosity_readings r
            JOIN (
                SELECT product_id, MAX(measured_date || ':' || printf('%012d', id)) AS max_key
                FROM viscosity_readings
                WHERE measured_date IS NOT NULL
                GROUP BY product_id
            ) pick
              ON pick.product_id = r.product_id
             AND pick.max_key = r.measured_date || ':' || printf('%012d', r.id)
        ) latest ON latest.product_id = p.id
        WHERE {" AND ".join(where)}
          AND NOT EXISTS (
              SELECT 1
              FROM viscosity_readings today
              WHERE today.product_id = p.id
                AND today.measured_date = ?
          )
        ORDER BY p.code ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "code": row["code"],
            "name": row["name"],
            "latest_value": _opt_float(row["latest_value"]),
            "latest_date": row["latest_date"],
        }
        for row in rows
    ]


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
    reactor: int | None = None,
) -> int:
    """점도 측정 1건 등록. measured_date 미지정 시 LOT 에서 추론, 실패 시 등록일.

    blend_record_id 지정 시 해당 배합 실적과 연계된다([[blend-overhaul]]).
    reactor 지정 시 반응기 번호(1~4)를 기록한다(반응기 진행 반제품).
    """
    # 측정일 폴백은 로컬 '오늘' — created_at(UTC) 을 자르면 자정 부근 하루 밀림.
    resolved_date = measured_date or parse_lot_date(lot_no) or date.today().isoformat()
    cur = connection.execute(
        """
        INSERT INTO viscosity_readings
            (product_id, lot_no, viscosity, measured_date, memo,
             recipe_material, material_lot, created_by, created_at, blend_record_id, reactor)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            int(reactor) if reactor is not None else None,
        ),
    )
    return int(cur.lastrowid)


def _actor_display(by: Any) -> str:
    """감사·excluded_by 표기용 책임자 이름. dict(current_user) 또는 문자열 모두 허용."""
    if isinstance(by, dict):
        return str(by.get("display_name") or by.get("username") or "책임자")
    return str(by) if by else "책임자"


def exclude_reading(
    connection: sqlite3.Connection,
    reading_id: int,
    reason: str,
    by: Any,
    now: str,
) -> dict[str, Any] | None:
    """측정 1건을 '통계 제외'로 표시(삭제 아님). 이후 평균/σ/관리한계/추세/집계에서 빠진다.

    reason 은 필수(비면 ValueError). 알 수 없는 id 면 None 반환.
    감사 로그(viscosity_reading_excluded)를 남긴다. 커밋은 호출자 책임.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("exclude reason required")
    row = connection.execute(
        "SELECT id, product_id, lot_no FROM viscosity_readings WHERE id = ?",
        (reading_id,),
    ).fetchone()
    if not row:
        return None
    by_name = _actor_display(by)
    connection.execute(
        "UPDATE viscosity_readings "
        "SET excluded = 1, exclude_reason = ?, excluded_by = ?, excluded_at = ? "
        "WHERE id = ?",
        (reason, by_name, now, reading_id),
    )
    from ..db import write_audit_log

    write_audit_log(
        connection,
        action="viscosity_reading_excluded",
        actor=by if isinstance(by, dict) else None,
        target_type="viscosity_reading",
        target_id=str(reading_id),
        target_label=str(row["lot_no"]),
        details={"reason": reason},
    )
    return {"id": int(row["id"]), "product_id": int(row["product_id"]), "lot_no": row["lot_no"]}


def include_reading(
    connection: sqlite3.Connection,
    reading_id: int,
    by: Any,
    now: str,
) -> dict[str, Any] | None:
    """측정 1건의 '통계 제외'를 해제 — 다시 통계에 포함된다. 알 수 없는 id 면 None.

    감사 로그(viscosity_reading_restored)를 남긴다. 커밋은 호출자 책임.
    """
    row = connection.execute(
        "SELECT id, product_id, lot_no, exclude_reason FROM viscosity_readings WHERE id = ?",
        (reading_id,),
    ).fetchone()
    if not row:
        return None
    connection.execute(
        "UPDATE viscosity_readings "
        "SET excluded = 0, exclude_reason = NULL, excluded_by = NULL, excluded_at = NULL "
        "WHERE id = ?",
        (reading_id,),
    )
    from ..db import write_audit_log

    write_audit_log(
        connection,
        action="viscosity_reading_restored",
        actor=by if isinstance(by, dict) else None,
        target_type="viscosity_reading",
        target_id=str(reading_id),
        target_label=str(row["lot_no"]),
        details={"prev_reason": row["exclude_reason"]},
    )
    return {"id": int(row["id"]), "product_id": int(row["product_id"]), "lot_no": row["lot_no"]}


def list_readings_for_blend(
    connection: sqlite3.Connection, blend_record_id: int
) -> list[dict[str, Any]]:
    """배합 실적에 연계된 점도 측정 목록 (제품 코드 포함)."""
    rows = connection.execute(
        """
        SELECT r.id, r.viscosity, r.measured_date, r.memo, r.lot_no, r.reactor, r.created_by,
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
            "reactor": r["reactor"],
            "product_id": int(r["product_id"]),
            "product_code": r["product_code"],
            "product_name": r["product_name"],
            "created_by": r["created_by"],
        }
        for r in rows
    ]
