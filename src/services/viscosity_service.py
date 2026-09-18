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


_PRODUCT_COLUMNS = (
    "id, code, name, target, lower_limit, upper_limit, sigma_k, rpm, temperature, "
    "remind_daily, use_reactor, is_active, created_at"
)


def _select_products(connection: sqlite3.Connection, tail: str, params: tuple) -> list[sqlite3.Row]:
    """viscosity_products 조회 — warn_low/warn_high(2026-09-08) 컬럼이 없는 구버전/단위테스트
    스키마 폴백(recipe_helpers.fetch_recipe_items 의 loss_comp_g 와 같은 2단 쿼리 패턴)."""
    try:
        return connection.execute(
            f"SELECT {_PRODUCT_COLUMNS}, warn_low, warn_high FROM viscosity_products {tail}", params
        ).fetchall()
    except sqlite3.OperationalError:
        return connection.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM viscosity_products {tail}", params
        ).fetchall()


def list_products(connection: sqlite3.Connection, *, active_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE is_active = 1" if active_only else ""
    rows = _select_products(
        connection, f"{where} ORDER BY is_active DESC, code ASC", ()
    )
    return [_serialize_product(connection, row) for row in rows]


def get_product(connection: sqlite3.Connection, product_id: int) -> dict[str, Any] | None:
    rows = _select_products(connection, "WHERE id = ?", (product_id,))
    return _serialize_product(connection, rows[0]) if rows else None


def get_product_by_code(connection: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    # GAP-4: 코드 비교는 strip+upper 정규화(대소문자·앞뒤 공백 무시)로 조회한다.
    # 리마인더 쿼리(daily_reading_reminders 의 upper(p.code))·자동 생성(ensure_product_by_code)이
    # 모두 같은 정규화를 쓰므로, product_name 이 대소문자/공백만 달라도 같은 논리적 제품으로
    # 귀결돼 중복 점도 제품이 생기지 않는다.
    normalized = str(code or "").strip().upper()
    rows = _select_products(connection, "WHERE upper(code) = ?", (normalized,))
    return _serialize_product(connection, rows[0]) if rows else None


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
        # 경고 문턱(고정값) — 관리 한계 안쪽의 '확인 필요' 구간. σ 경고와 달리 표본 무관.
        "warn_low": _opt_float(row["warn_low"]) if "warn_low" in row.keys() else None,
        "warn_high": _opt_float(row["warn_high"]) if "warn_high" in row.keys() else None,
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


def _row_value(row: Any, key: str) -> Any:
    """행에서 열 값을 안전하게 읽는다(열이 없으면 None) — _is_excluded 와 같은 방식."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


_OPTIONAL_READING_COLUMNS = (
    "excluded",
    "exclude_reason",
    "excluded_by",
    "excluded_at",
    "reviewed_at",
    "reviewed_by",
    "review_note",
    "blend_record_id",
)


def _has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    """테이블에 컬럼이 있는가(PRAGMA). 구버전·단위테스트 스키마 폴백용."""
    try:
        return column in {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
    except sqlite3.OperationalError:
        return False


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


def _blend_not_test_clause(connection: sqlite3.Connection, alias: str = "") -> str:
    """시험 **배합 기록**을 빼는 WHERE 조각(blend_service.not_test_clause 와 같은 규약).

    점도 쪽에서 blend_records 를 조인할 때 쓴다. 컬럼·테이블이 없는 최소 스키마는 '1=1'.
    """
    if not _has_table(connection, "blend_records"):
        return "1=1"
    if not _has_column(connection, "blend_records", "is_test"):
        return "1=1"
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}is_test, 0) = 0"


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
    # 나중에 추가된 열(제외·확인 처리·배합 연계)은 구 스키마/단위테스트 스키마에 없을 수
    # 있다 — 있는 열만 고르고 없는 열은 NULL 로 채워 호출부가 같은 키로 읽게 한다.
    existing = {
        row[1]
        for row in connection.execute("PRAGMA table_info(viscosity_readings)").fetchall()
    }
    optional_select = ", ".join(
        col if col in existing else f"NULL AS {col}" for col in _OPTIONAL_READING_COLUMNS
    )
    # 1차 구현(v1)이 남긴 is_test 열(운영 DB 에는 있다). 시험 값은 기동 마이그레이션
    # (migrate_v1_test_viscosity)이 test_viscosity_readings 로 옮기고, 옮기지 못한 고아 행만
    # 여기 남는다 — 통계·판정에 섞이지 않게 이 한 곳에서 뺀다. 열이 없으면 조건도 없다.
    legacy_test_clause = "AND COALESCE(is_test, 0) = 0" if "is_test" in existing else ""
    return connection.execute(
        f"""
        SELECT id, product_id, lot_no, viscosity, measured_date,
               memo, recipe_material, material_lot, reactor, created_by, created_at,
               {optional_select}
        FROM viscosity_readings
        WHERE product_id = ? {year_clause} {reactor_clause} {legacy_test_clause}
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


def _is_spec_out(value: float, product: dict[str, Any]) -> bool:
    """이 값이 규격(사용 금지) 밖인가. 판정과 같은 부등호(경계 포함)를 쓴다."""
    lower = product.get("lower_limit")
    upper = product.get("upper_limit")
    if lower is not None and value <= float(lower):
        return True
    if upper is not None and value >= float(upper):
        return True
    return False


def baseline_values(product: dict[str, Any], values: list[float]) -> tuple[list[float], int]:
    """중심·σ 를 만들 표본을 고른다. 반환: (표본, 규격 밖이라 뺀 개수).

    규격 밖(사용 금지) 값은 정상 변동이 아니라 사건이다. 그걸 기준에 넣으면 그 값을
    잡아야 할 σ 를 스스로 넓혀, 관리 하한이 사용 금지선 아래로 내려간다
    (APB17 2026: 2건 때문에 σ 11.99, 하한 326.6 → 뺀 뒤 10.58, 331.8. 2026-09-10 현장 지적).
    다만 표본이 너무 적어지면 기준 자체가 사라지므로, 남는 수가 σ 최소 표본에 못 미치면
    전부 쓴다(기준 없음보다 오염된 기준이 낫다).
    """
    kept = [v for v in values if not _is_spec_out(v, product)]
    dropped = len(values) - len(kept)
    if dropped and len(kept) < MIN_SIGMA_SAMPLES:
        return list(values), 0
    return kept, dropped


def _control_limits(product: dict[str, Any], values: list[float]) -> dict[str, Any]:
    """제품 설정 + 표본으로부터 중심선/통계 관리한계를 산출.

    중심·σ 는 규격 안 값으로만 만든다(baseline_values). n 은 표본 전체 수를 그대로 둔다 —
    화면의 '측정 건수'와 σ 축적 안내가 유효 측정 수를 말해야 하기 때문.
    """
    n = len(values)
    base, spec_out = baseline_values(product, values)
    mean = statistics.fmean(base) if base else None
    std = statistics.stdev(base) if len(base) >= 2 else 0.0
    center = product["target"] if product["target"] is not None else mean
    sigma_k = product["sigma_k"]

    ucl = lcl = uwl = lwl = None
    sigma_ready = len(base) >= MIN_SIGMA_SAMPLES
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

    # 고정 경고 문턱(warn_low/warn_high, 2026-09-08)을 경고 밴드에 합친다 — 관리 기준 그림·
    # 문구가 "48 이하는 경고"를 같이 말해야 한다(σ 2σ 선이 48 아래에 있으면 그 사이가 초록으로
    # 보여 판정과 어긋났다). 경고선은 안쪽 것이 이긴다: 하한은 큰 쪽, 상한은 작은 쪽.
    # 관리 한계(lcl/ucl, 이상 기준)는 건드리지 않는다 — 경고와 이상은 다른 층위다.
    warn_low = product.get("warn_low")
    warn_high = product.get("warn_high")
    if warn_low is not None:
        lwl = warn_low if lwl is None else max(lwl, warn_low)
    if warn_high is not None:
        uwl = warn_high if uwl is None else min(uwl, warn_high)

    # 이상으로 판정되는 실제 경계 — 규격과 σ 중 안쪽. 밴드 그림이 쓰는 값과 같다.
    # 화면이 "몇부터 이상인가"를 한 줄로 말할 수 있게 서버가 함께 준다.
    lower_spec = product.get("lower_limit")
    upper_spec = product.get("upper_limit")
    low_cands = [v for v in (lower_spec, lcl) if v is not None]
    high_cands = [v for v in (upper_spec, ucl) if v is not None]
    anomaly_low = max(low_cands) if low_cands else None
    anomaly_high = min(high_cands) if high_cands else None

    return {
        "n": n,
        # 중심·σ 를 만든 표본 수와, 규격 밖이라 기준에서 뺀 개수(화면 안내용).
        "baseline_n": len(base),
        "spec_out_n": spec_out,
        # 이상 판정 경계(규격과 σ 중 안쪽). None 이면 그쪽 경계가 없다.
        "anomaly_low": round(anomaly_low, 3) if anomaly_low is not None else None,
        "anomaly_high": round(anomaly_high, 3) if anomaly_high is not None else None,
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

    # 관리 한계는 경계 포함(2026-09-08): 현장 규칙이 "340 이하는 사용 금지"처럼 '이하/이상'
    # 으로 말해지므로 경고 문턱(warn_low/high)과 같은 부등호를 쓴다. 운영 DB 에는 그때까지
    # 관리 한계를 둔 반제품이 없어 기존 판정이 바뀌는 곳은 없다.
    upper = product["upper_limit"]
    lower = product["lower_limit"]
    if upper is not None and value >= upper:
        reasons.append("spec_high")
        side = "high"
    if lower is not None and value <= lower:
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

    # 고정 경고 문턱 — 제품 설정의 warn_low/warn_high. 표본·σ 와 무관하게 항상 적용
    # (PB 48 이하 → 경고, 2026-09-08). 관리 한계(이상)를 이미 넘긴 값은 위에서 걸렸다.
    warn_low = product.get("warn_low")
    warn_high = product.get("warn_high")
    if warn_high is not None and value >= warn_high:
        return {"status": "warn", "side": "high", "reasons": ["warn_high_limit"]}
    if warn_low is not None and value <= warn_low:
        return {"status": "warn", "side": "low", "reasons": ["warn_low_limit"]}

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
    prev_normal_mean: float | None = None
    for key in sorted(buckets):
        items = buckets[key]
        values = [x["viscosity"] for x in items]
        mean = round(statistics.fmean(values), 3)
        std = round(statistics.stdev(values), 3) if len(values) >= 2 else 0.0
        # 전기 대비(mean_delta)는 **이상(anomaly) 측정을 뺀 평균** 기준 — 이상값이
        # 낀 구간 평균으로 비교하면 그 다음 정상 구간이 "+270" 같은 무의미한 급변으로
        # 표시된다(2026-08-13 검토: 7/24 이상 128.4 → 7/27 정상 398.4 가 ▲+270).
        # 표시용 평균(mean)·범위는 실측 그대로 두고, 비교 기준만 정상 표본으로 좁힌다.
        # 구간 전체가 이상이면 비교 기준을 갱신하지 않는다(다음 정상 구간은 마지막
        # 정상 기준과 비교).
        normal_values = [x["viscosity"] for x in items if x["status"] != "anomaly"]
        normal_mean = (
            round(statistics.fmean(normal_values), 3) if normal_values else None
        )
        delta = (
            None
            if normal_mean is None or prev_normal_mean is None
            else round(normal_mean - prev_normal_mean, 3)
        )
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
        if normal_mean is not None:
            prev_normal_mean = normal_mean
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


# 바인더(APB/CSPB 등)의 '사용한 PB' 연계가 참조하는 소스 반제품 코드.
# _pb_viscosity_map · 배합 상세에서의 PB 행 감지가 같은 기준을 쓴다(단일 원천).
SOURCE_PB_CODE = "PB"


def detect_source_pb_lot(details: list[dict[str, Any]]) -> tuple[str | None, str]:
    """배합 상세에서 '사용한 PB' 자재 LOT 을 찾는다. 반환: (lot, method).

    method: 'matched' — 자재명/코드가 PB(소스 반제품 코드)와 일치하는 행에서 찾음
            'none'    — 레시피에 PB 자재가 없음(= 이 배합은 PB 연계 대상이 아님)

    PB 행이 없으면 **연계 없음이 정답**이다. 첫 계량 자재 폴백(2026-08-13 도입)은
    PB 를 쓰지 않는 대부분의 품목 — 심지어 PB 자신 — 에까지 "첫 자재로 추정"
    경고를 띄우는 소음이 됐다(2026-08-14 현장 지적). 자재명은 이름 하나 원칙으로
    정본화돼 있어 PB 행은 이름 매칭으로 충분하다. 화면 보정(material_lot 수동
    입력, method='manual')은 등록 API 에서 계속 지원한다.
    """
    for d in details or []:
        name = str(d.get("material_name") or "").strip().upper()
        code = str(d.get("material_code") or "").strip().upper()
        if name == SOURCE_PB_CODE or code == SOURCE_PB_CODE:
            lot = str(d.get("material_lot") or "").strip()
            return (lot or None), ("matched" if lot else "none")
    return None, "none"


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
    pb = get_product_by_code(connection, SOURCE_PB_CODE)
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


def product_lot_alert(connection: sqlite3.Connection, product_name: str, lot: str) -> dict[str, Any]:
    """반제품 LOT 하나의 점도 경고 — 배합 화면이 자재 LOT 을 넣을 때 그 행 아래에 띄운다.

    사용자 요청(2026-09-08): PB 를 쓰는 품목의 배합에서 PB LOT 을 입력하면 그 PB 의 점도가
    경고 하한(48) 이하인지 작업자가 바로 알아야 한다. 판정은 제품 설정의 고정 기준만 쓴다
    (관리 한계 → 이상, 경고 문턱 → 경고). σ 는 표본 따라 움직여 현장 안내로는 부적합.

    측정은 lot_no 정확 일치 우선, 없으면 숫자 8자리(_lot_digits) 일치. 통계 제외된 측정도
    본다 — 제외는 통계용이고, 작업자에게는 "그 LOT 이 실제로 잰 값"이 중요하다.
    """
    name = str(product_name or "").strip()
    lot = str(lot or "").strip()
    none = {"found": False, "product": None, "viscosity": None, "level": None,
            "reason": None, "threshold": None, "message": None}
    if not name or not lot:
        return none
    product = get_product_by_code(connection, name)
    if not product:
        return none
    row = connection.execute(
        "SELECT viscosity, lot_no FROM viscosity_readings WHERE product_id = ? AND lot_no = ? "
        "ORDER BY measured_date DESC, id DESC LIMIT 1",
        (product["id"], lot),
    ).fetchone()
    if row is None:
        digits = _lot_digits(lot)
        if digits:
            rows = connection.execute(
                "SELECT viscosity, lot_no FROM viscosity_readings WHERE product_id = ? "
                "ORDER BY measured_date DESC, id DESC",
                (product["id"],),
            ).fetchall()
            row = next((r for r in rows if _lot_digits(r["lot_no"]) == digits), None)
    if row is None:
        return dict(none, product=product["code"])
    value = float(row["viscosity"])
    level = reason = None
    threshold = None
    if product["lower_limit"] is not None and value <= product["lower_limit"]:
        level, reason, threshold = "anomaly", "관리 하한 이하", product["lower_limit"]
    elif product["upper_limit"] is not None and value >= product["upper_limit"]:
        level, reason, threshold = "anomaly", "관리 상한 이상", product["upper_limit"]
    elif product["warn_low"] is not None and value <= product["warn_low"]:
        level, reason, threshold = "warn", "경고 하한 이하", product["warn_low"]
    elif product["warn_high"] is not None and value >= product["warn_high"]:
        level, reason, threshold = "warn", "경고 상한 이상", product["warn_high"]
    message = None
    if level:
        # 배합 화면 LOT 칸 아래 한 줄로 들어가야 하므로 짧게: "PB 점도 47.5 · 경고 하한 48 이하".
        # 관리 한계 이탈은 현장 말로 '사용 금지'(APB17 340 이하, 2026-09-08)를 앞세운다.
        thr = f"{threshold:g}"
        word = reason.replace("경고 하한 이하", f"경고 하한 {thr} 이하").replace(
            "경고 상한 이상", f"경고 상한 {thr} 이상").replace(
            "관리 하한 이하", f"사용 금지 (관리 하한 {thr} 이하)").replace(
            "관리 상한 이상", f"사용 금지 (관리 상한 {thr} 이상)")
        message = f"{product['code']} 점도 {value:g} · {word}"
    return {
        "found": True, "product": product["code"], "viscosity": value,
        "level": level, "reason": reason, "threshold": threshold, "message": message,
    }


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
    pb_map = (
        _pb_viscosity_map(connection)
        if product.get("code") != SOURCE_PB_CODE
        else {}
    )

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
            # 확인 처리(실제 이상으로 보고 조치함) — 판정·통계와 무관한 표시일 뿐이다.
            "reviewed": bool(_row_value(r, "reviewed_at")),
            "reviewed_by": _row_value(r, "reviewed_by"),
            "reviewed_at": _row_value(r, "reviewed_at"),
            "review_note": _row_value(r, "review_note"),
            "blend_record_id": _row_value(r, "blend_record_id"),
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
        # 아직 확인 처리되지 않은 이상 — 대시보드가 세는 '할 일' 건수.
        "anomaly_unreviewed": sum(1 for x in anomalies if not x["reviewed"]),
    }
    # 기간 집계도 유효 측정만으로 — 제외된 이상이 기간 평균/σ 를 밀어올리지 않게 한다.
    periods = summarize_periods(valid_readings, granularity)
    control["excluded_n"] = excluded_count

    # PB 연계 요약 — 화면이 "왜 연계가 안 보이는지"를 말할 수 있게 한다.
    # 종전에는 매칭 0건이면 패널이 조용히 숨겨져 실패가 무증상이었다(2026-08-13 검토).
    with_lot = sum(1 for x in readings if (x.get("material_lot") or "").strip())
    matched = sum(1 for x in readings if x.get("source_pb_viscosity") is not None)
    pb_link = {
        "source_code": SOURCE_PB_CODE,
        "source_exists": bool(pb_map) or bool(
            get_product_by_code(connection, SOURCE_PB_CODE)
        ),
        "readings_with_lot": with_lot,
        "matched": matched,
    }

    return {
        "pb_link": pb_link,
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
    total_anomaly_unreviewed = 0
    for product in products:
        years = available_years(connection, product["id"])
        latest_year = years[0] if years else None
        analysis = analyze_product(connection, product, year=latest_year)
        readings = analysis["readings"]
        last = readings[-1] if readings else None
        anomaly_count = analysis["counts"]["anomaly"]
        anomaly_unreviewed_count = analysis["counts"]["anomaly_unreviewed"]
        total_anomaly += anomaly_count
        total_anomaly_unreviewed += anomaly_unreviewed_count
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
            "anomaly_unreviewed_count": anomaly_unreviewed_count,
            "warn_count": analysis["counts"]["warn"],
            "trend_count": len(analysis["trends"]),
        })
    return {
        "items": items,
        "total_anomaly": total_anomaly,
        "total_anomaly_unreviewed": total_anomaly_unreviewed,
        "product_count": len(items),
    }


def daily_reading_reminders(
    connection: sqlite3.Connection,
    *,
    target_date: str,
    codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """측정 안 된 배합 LOT(미등록 LOT)이 하나라도 남은 '매일 알림 대상'(remind_daily=1)
    반제품 목록.

    판정 단위는 제품이 아니라 **LOT** 이다(2026-08-19 재정의). 종전 '오늘(target_date)
    측정이 없는 품목' 조건은 LOT 등록 상태와 무관했다 — 어제 LOT 을 이미 등록했는데도
    오늘 등록이 없으면 알림이 다시 뜨고(독촉 소음), 반대로 못 잰 옛 LOT 이 하나 남아
    있으면 그것만으로 매일 알림이 이어졌다. 이제 "해야 할 일"이 실제로 있는지, 즉
    **pending LOT**(완료 배합 중 점도 등록도 측정 불가 기록도 없는 것)이 하나라도
    있는지만 본다. '오늘 측정 여부' 조건은 완전히 없앴다.

    pending LOT 조건은 /viscosity 등록 패널의 미등록 대기열(viscosity_blend_records,
    src/routers/viscosity_routes.py)과 같다 — status='completed',
    COALESCE(is_bulk_regenerated,0)=0, 반제품명이 점도 제품 name/code 와 일치,
    viscosity_readings.blend_record_id 로 연결된 등록 없음, viscosity_skips 측정 불가
    기록 없음. 여기에 알림용 시간 조건 두 가지만 얹는다.

    - 배합 **당일은 알리지 않는다**(work_date < target_date) — 점도는 배합 다음날
      측정하는 현장 규칙(예: 13일 배합 → 14일 측정, 2026-08-13 요청).
    - 정리 기준일(app_settings.viscosity_reminder_since)이 있으면 그 이후 배합만
      본다(2026-08-07). 지나간 배합은 이제 와서 잴 수 없으니 책임자가
      [지금까지 정리] 를 누른 시점 이전은 덮는다.

    알림 대상 여부는 웹 점도 설정이 소유한다(remind_daily 플래그). codes 는 선택적
    추가 필터일 뿐이며, 비어 있으면 알림 대상 전체를 대상으로 한다(서버 주도).

    각 항목은 pending_count(전체 pending LOT 수)와 pending_lots(오래된 순 최대
    10건: blend_record_id/product_lot/work_date/reactor)를 함께 실는다 — 트레이
    팝업이 무엇 때문에 알림이 떴는지 보여줄 수 있게. blend_records 테이블이 없는
    최소 스키마에서는 pending 정의 자체가 불가능하므로 빈 목록을 반환한다(방어
    패턴 — viscosity_skips 없는 구버전과 동일).
    """
    has_blend_records = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='blend_records'"
    ).fetchone()
    if not has_blend_records:
        return []

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

    # pending LOT 조건 — 미등록 대기열(viscosity_routes.viscosity_blend_records)과
    # 같은 조건에 알림용 시간 조건(당일 제외·정리 기준일)을 얹은 것.
    lot_where = [
        "b.status = 'completed'",
        "COALESCE(b.is_bulk_regenerated, 0) = 0",
        "(b.product_name = p.name OR b.product_name = p.code)",
        "b.work_date < ?",  # 배합 당일 제외 — 측정은 다음날부터
        "NOT EXISTS (SELECT 1 FROM viscosity_readings vr"
        "            WHERE vr.blend_record_id = b.id)",
        # 시험 배합은 알림 대상이 아니다(계약 §9-3) — 트레이가 읽는 pending_lots 에
        # 시험 LOT 이 섞이면 현장은 정식 LOT 을 못 잰 것으로 읽는다. 시험명은 반제품
        # 이름과 다르므로 위 이름 조건에 자연히 걸리지 않지만, 우연한 동명에도 막힌다.
        # (시험 점도 자체는 test_viscosity_readings 에만 있어 아래 최근값에는 섞일 수 없다.)
        f"{_blend_not_test_clause(connection, 'b')}",
    ]
    lot_params: list[Any] = [target_date]
    since = settings_service.get_viscosity_reminder_since(connection)
    if since:
        lot_where.append("b.work_date >= ?")  # 정리 기준일 이후 배합만
        lot_params.append(since)
    # 측정 불가로 기록된 배합(viscosity_skips)은 pending 에서 뺀다 — 시료가 없어
    # 잴 수 없는 배합 하나가 남아 알림이 영원히 오던 문제(2026-08-14). 그 배합은
    # '등록된 것'과 동일하게 취급되고, 새 배합이 생기면 알림은 재개된다.
    # (테이블이 없는 구버전/최소 스키마 DB 는 조건 없이 — 방어 패턴 공통.)
    has_skips = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='viscosity_skips'"
    ).fetchone()
    if has_skips:
        lot_where.append(
            "NOT EXISTS (SELECT 1 FROM viscosity_skips s"
            "            WHERE s.blend_record_id = b.id)"
        )
    lot_condition = " AND ".join(lot_where)

    # 한 번의 조인으로 제품별 pending LOT 을 모두 가져와 파이썬에서 묶는다.
    # 정렬(p.code ASC, work_date ASC, id ASC)이 곧 항목 순서·LOT 순서(오래된 것부터
    # 최대 10건)를 결정한다.
    rows = connection.execute(
        f"""
        SELECT
            p.id,
            p.code,
            p.name,
            b.id AS blend_record_id,
            b.product_lot,
            b.work_date,
            b.reactor,
            latest.viscosity AS latest_value,
            latest.measured_date AS latest_date
        FROM viscosity_products p
        JOIN blend_records b ON {lot_condition}
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
        ORDER BY p.code ASC, b.work_date ASC, b.id ASC
        """,
        [*lot_params, *params],
    ).fetchall()

    items: list[dict[str, Any]] = []
    items_by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = items_by_id.get(row["id"])
        if item is None:
            item = {
                "id": int(row["id"]),
                "code": row["code"],
                "name": row["name"],
                "latest_value": _opt_float(row["latest_value"]),
                "latest_date": row["latest_date"],
                "pending_count": 0,
                "pending_lots": [],
            }
            items_by_id[item["id"]] = item
            items.append(item)
        item["pending_count"] += 1
        if len(item["pending_lots"]) < 10:
            item["pending_lots"].append({
                "blend_record_id": int(row["blend_record_id"]),
                "product_lot": row["product_lot"],
                "work_date": row["work_date"],
                "reactor": int(row["reactor"]) if row["reactor"] is not None else None,
            })
    return items


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

    시험 배합 LOT 은 받지 않는다(TestLotError, 계약 §9-2). 시험 점도는
    test_viscosity_readings 에만 산다 — 여기서 막으면 정식 통계·관리한계·이상·추세·
    알림·대시 카드가 시험 값을 볼 길이 없다(측정값 단위 필터는 v1 이 남긴 고아 행용
    _fetch_readings 한 곳뿐). 판정은 등록 경로가 아니라 LOT 자체가 한다(배합 연계 등록·
    직접 등록·엑셀 임포트 모두 이 함수를 지난다).
    """
    if is_test_blend_lot(connection, lot_no, blend_record_id):
        raise TestLotError(TEST_LOT_DETAIL)
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


# 정식 점도에 시험 LOT 이 들어오려 할 때의 안내 — 읽는 사람이 갈 곳을 말한다.
TEST_LOT_DETAIL = "시험 LOT은 시험 탭에서 등록하세요."


class TestLotError(ValueError):
    """정식 점도(viscosity_readings)에 시험 배합 LOT 을 넣으려 했다(계약 §9-2).

    호출하는 라우트는 400 으로 바꾼다. 엑셀 임포트 스크립트(scripts/import_*viscosity.py)
    는 이 오류에서 멈추고, 끝에 한 번만 커밋하므로 그 실행분은 적재되지 않는다(시험 LOT
    은 'T-' 로 시작해 옛 엑셀 LOT 과 겹칠 일이 없다).
    """

    # pytest 가 'Test' 로 시작하는 이름을 테스트 클래스로 모으지 않게 한다.
    __test__ = False


def is_test_blend_lot(
    connection: sqlite3.Connection, lot_no: Any, blend_record_id: int | None = None
) -> bool:
    """이 LOT 또는 연계 배합 기록이 시험 배합인가.

    product_lot 은 전역 유일이므로 LOT 이 시험 기록의 제품 LOT 과 같으면 시험이다.
    연계 등록(blend_record_id)은 그 기록이 시험이어도 시험이다. blend_records 나
    is_test 컬럼이 없는 최소 스키마에서는 시험 기록이 있을 수 없으므로 False.
    """
    if not _has_table(connection, "blend_records"):
        return False
    if not _has_column(connection, "blend_records", "is_test"):
        return False
    clauses: list[str] = []
    params: list[Any] = []
    lot = str(lot_no or "").strip()
    if lot:
        clauses.append("product_lot = ?")
        params.append(lot)
    if blend_record_id is not None:
        clauses.append("id = ?")
        params.append(int(blend_record_id))
    if not clauses:
        return False
    row = connection.execute(
        "SELECT 1 FROM blend_records "
        f"WHERE COALESCE(is_test, 0) = 1 AND ({' OR '.join(clauses)}) LIMIT 1",
        params,
    ).fetchone()
    return row is not None


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


def review_reading(
    connection: sqlite3.Connection,
    reading_id: int,
    note: str,
    by_name: str,
    now: str,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """이상 측정 1건을 '확인 처리' — 실제 이상으로 보고 조치했다는 표시.

    통계 제외와 달리 판정·통계는 그대로(이상으로 남는다). 대시보드 미확인 건수에서만 빠진다.
    note(조치 내용)는 strip 후 2자 이상(아니면 ValueError). 알 수 없는 id 면 None.
    감사 로그(viscosity_reading_reviewed)를 남긴다. 커밋은 호출자 책임.
    """
    note = (note or "").strip()
    if len(note) < 2:
        raise ValueError("review note must be at least 2 characters")
    row = connection.execute(
        "SELECT id, product_id, lot_no FROM viscosity_readings WHERE id = ?",
        (reading_id,),
    ).fetchone()
    if not row:
        return None
    connection.execute(
        "UPDATE viscosity_readings "
        "SET reviewed_at = ?, reviewed_by = ?, review_note = ? "
        "WHERE id = ?",
        (now, by_name, note, reading_id),
    )
    from ..db import write_audit_log

    write_audit_log(
        connection,
        action="viscosity_reading_reviewed",
        actor=actor if isinstance(actor, dict) else None,
        target_type="viscosity_reading",
        target_id=str(reading_id),
        target_label=str(row["lot_no"]),
        details={"note": note[:300], "by": by_name},
    )
    return {"id": int(row["id"]), "product_id": int(row["product_id"]), "lot_no": row["lot_no"]}


def unreview_reading(
    connection: sqlite3.Connection,
    reading_id: int,
    by: Any,
    now: str,
) -> dict[str, Any] | None:
    """'확인 처리'를 취소 — 다시 미확인 이상으로 센다. 알 수 없는 id 면 None.

    감사 로그(viscosity_reading_review_cleared)를 남긴다. 커밋은 호출자 책임.
    """
    row = connection.execute(
        "SELECT id, product_id, lot_no, review_note, reviewed_by "
        "FROM viscosity_readings WHERE id = ?",
        (reading_id,),
    ).fetchone()
    if not row:
        return None
    connection.execute(
        "UPDATE viscosity_readings "
        "SET reviewed_at = NULL, reviewed_by = NULL, review_note = NULL "
        "WHERE id = ?",
        (reading_id,),
    )
    from ..db import write_audit_log

    write_audit_log(
        connection,
        action="viscosity_reading_review_cleared",
        actor=by if isinstance(by, dict) else None,
        target_type="viscosity_reading",
        target_id=str(reading_id),
        target_label=str(row["lot_no"]),
        details={"prev_note": row["review_note"], "prev_by": row["reviewed_by"]},
    )
    return {"id": int(row["id"]), "product_id": int(row["product_id"]), "lot_no": row["lot_no"]}


ANOMALY_STATES = ("unreviewed", "reviewed", "excluded", "all")


def list_anomalies(
    connection: sqlite3.Connection,
    *,
    state: str = "unreviewed",
    product_id: int | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """전 제품을 가로지른 이상 측정 목록 — 어느 제품·LOT·날짜인지 한 번에 본다.

    범위: 사용 중 제품(product_id 지정 시 그 제품만, 사용 안 함이어도). 연도는 지정값,
    없으면 제품별 최신 연도(overview 와 같은 규칙). state 로 미확인/확인/제외/전체를 고르고,
    counts 는 state 와 무관하게 같은 범위의 세 갈래 건수를 준다. 잘못된 state 는 ValueError.
    """
    if state not in ANOMALY_STATES:
        raise ValueError(f"invalid anomaly state: {state}")
    if product_id is not None:
        product = get_product(connection, int(product_id))
        products = [product] if product else []
    else:
        products = list_products(connection, active_only=True)

    items: list[dict[str, Any]] = []
    counts = {"unreviewed": 0, "reviewed": 0, "excluded": 0}
    for product in products:
        if year is not None:
            target_year = year
        else:
            years = available_years(connection, product["id"])
            target_year = years[0] if years else None
        analysis = analyze_product(connection, product, year=target_year)
        stats = analysis["stats"]
        anomalies = analysis["anomalies"]
        unreviewed = [x for x in anomalies if not x["reviewed"]]
        reviewed = [x for x in anomalies if x["reviewed"]]
        excluded = [x for x in analysis["readings"] if x["status"] == "excluded"]
        counts["unreviewed"] += len(unreviewed)
        counts["reviewed"] += len(reviewed)
        counts["excluded"] += len(excluded)
        if state == "unreviewed":
            candidates = unreviewed
        elif state == "reviewed":
            candidates = reviewed
        elif state == "excluded":
            candidates = excluded
        else:
            candidates = list(anomalies) + excluded
        for x in candidates:
            items.append({
                "reading_id": x["id"],
                "product_id": product["id"],
                "product_code": product["code"],
                "product_name": product["name"],
                "year": target_year,
                "lot_no": x["lot_no"],
                "measured_date": x["measured_date"],
                "viscosity": x["viscosity"],
                "status": x["status"],
                "side": x["side"],
                "reasons": x["reasons"],
                "anomaly_low": stats.get("anomaly_low"),
                "anomaly_high": stats.get("anomaly_high"),
                "reviewed": x["reviewed"],
                "reviewed_by": x["reviewed_by"],
                "reviewed_at": x["reviewed_at"],
                "review_note": x["review_note"],
                "excluded": x["excluded"],
                "exclude_reason": x["exclude_reason"],
                "excluded_by": x["excluded_by"],
                "excluded_at": x["excluded_at"],
                "blend_record_id": x["blend_record_id"],
            })

    # 최신 측정일 먼저(날짜 없음은 맨 뒤), 같은 날은 id 큰 것 먼저.
    items.sort(key=lambda it: -int(it["reading_id"]))
    items.sort(key=lambda it: it["measured_date"] or "", reverse=True)
    items.sort(key=lambda it: it["measured_date"] is None)
    return {"items": items, "counts": counts, "state": state}


def list_readings_for_blend(
    connection: sqlite3.Connection, blend_record_id: int
) -> list[dict[str, Any]]:
    """배합 실적에 연계된 점도 측정 목록 (제품 코드 포함).

    시험 배합 기록이면 시험 점도(test_viscosity_readings)를 **같은 모양**으로 앞에 싣는다
    (계약 §9-6). 반제품이 없으므로 product_code 자리에 '시험' 을 둔다 — 기록 조회 상세의
    '점도 측정' 칸이 정식과 같은 줄로 그린다. 정식 기록에는 시험 행이 없으니 그대로다.
    """
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
    return _test_readings_for_blend(connection, blend_record_id) + [
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


# ── 시험 배합 LOT 점도(2026-09-18 2차 결정, docs/test-blend-design.md §9) ──────────
# 시험 점도는 반제품(PB·SBCT·SCRA …)과 무관하게 **시험 LOT 하나에 값 하나**다. 기준
# 레시피가 없는 새 레시피 시험도 등록된다. 비교할 반제품 기준이 없을 수 있으므로 판정·
# σ·요약 숫자·규격선은 만들지 않는다 — 값과 누가 언제 쟀는지만 남긴다.
# 등록·정정·삭제 권한은 정식과 같다(라우트가 정식 규칙을 그대로 적용한다).

TEST_VISCOSITY_LABEL = "시험"   # 기록 조회 상세에서 반제품 코드 자리에 들어가는 표기
TEST_RECORD_LIMIT_MAX = 200


class TestViscosityError(ValueError):
    """시험 점도를 등록할 수 없는 기록 — detail 은 화면에 그대로 보여 줄 문장.

    status 는 라우트가 그대로 쓰는 HTTP 코드(404 기록 없음 · 400 시험 아님/취소 ·
    409 이미 등록).
    """

    __test__ = False  # pytest 가 테스트 클래스로 모으지 않게

    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _test_viscosity_ready(connection: sqlite3.Connection) -> bool:
    """시험 점도를 다룰 스키마가 있는가(최소 스키마·구버전 DB 폴백)."""
    return (
        _has_table(connection, "test_viscosity_readings")
        and _has_table(connection, "blend_records")
        and _has_column(connection, "blend_records", "is_test")
    )


def get_test_reading(
    connection: sqlite3.Connection, blend_record_id: int
) -> dict[str, Any] | None:
    """시험 LOT 의 점도 한 건 + 그 기록의 제품 LOT(없으면 None)."""
    if not _has_table(connection, "test_viscosity_readings"):
        return None
    row = connection.execute(
        "SELECT tv.id, tv.blend_record_id, tv.viscosity, tv.measured_date, tv.memo, "
        "tv.created_by, tv.created_at, tv.updated_by, tv.updated_at, br.product_lot "
        "FROM test_viscosity_readings tv "
        "LEFT JOIN blend_records br ON br.id = tv.blend_record_id "
        "WHERE tv.blend_record_id = ?",
        (int(blend_record_id),),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "blend_record_id": int(row["blend_record_id"]),
        "product_lot": row["product_lot"],
        "viscosity": float(row["viscosity"]),
        "measured_date": row["measured_date"],
        "memo": row["memo"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def _test_readings_for_blend(
    connection: sqlite3.Connection, blend_record_id: int
) -> list[dict[str, Any]]:
    """list_readings_for_blend 모양의 시험 점도 행(0 또는 1건)."""
    reading = get_test_reading(connection, blend_record_id)
    if reading is None:
        return []
    return [
        {
            "id": reading["id"],
            "viscosity": reading["viscosity"],
            "measured_date": reading["measured_date"],
            "memo": reading["memo"],
            "lot_no": reading["product_lot"],
            "reactor": None,
            "product_id": None,
            "product_code": TEST_VISCOSITY_LABEL,
            "product_name": TEST_VISCOSITY_LABEL,
            "created_by": reading["created_by"],
        }
    ]


def list_test_records(
    connection: sqlite3.Connection,
    *,
    q: str | None = None,
    unregistered: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """점도 화면 '시험' 탭 목록 — 완료된 시험 배합 기록 전부(반제품 선택과 무관).

    최신순(work_date, id 내림차순). q 는 제품 LOT·시험명·작업자 부분 일치,
    unregistered 는 점도가 아직 없는 LOT 만. 건수(total·unregistered_total)는 q 를
    반영하되 unregistered 와는 무관하다(정식 등록 대기열과 같은 규약).
    기준 레시피 이름은 base_recipe_id 의 레시피 제품명(없거나 지워졌으면 None).
    """
    limit = max(1, min(int(limit or 20), TEST_RECORD_LIMIT_MAX))
    empty = {"items": [], "total": 0, "unregistered_total": 0, "limit": limit}
    if not _test_viscosity_ready(connection):
        return empty
    where = ["COALESCE(br.is_test, 0) = 1", "br.status = 'completed'"]
    params: list[Any] = []
    query = (q or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            "(br.product_lot LIKE ? OR br.product_name LIKE ? OR br.worker LIKE ?)"
        )
        params.extend([like, like, like])
    where_sql = " AND ".join(where)
    registered_sql = (
        "EXISTS (SELECT 1 FROM test_viscosity_readings tv0 "
        "        WHERE tv0.blend_record_id = br.id)"
    )
    total = connection.execute(
        f"SELECT COUNT(*) FROM blend_records br WHERE {where_sql}", params
    ).fetchone()[0]
    unregistered_total = connection.execute(
        f"SELECT COUNT(*) FROM blend_records br WHERE {where_sql} AND NOT {registered_sql}",
        params,
    ).fetchone()[0]
    has_base = _has_column(connection, "blend_records", "base_recipe_id")
    base_id_sql = "br.base_recipe_id" if has_base else "NULL"
    base_name_sql = (
        "(SELECT r.product_name FROM recipes r WHERE r.id = br.base_recipe_id)"
        if has_base and _has_table(connection, "recipes")
        else "NULL"
    )
    rows = connection.execute(
        f"""
        SELECT br.id, br.product_lot, br.product_name, br.work_date, br.worker,
               {base_id_sql} AS base_recipe_id,
               {base_name_sql} AS base_recipe_name,
               tv.id AS reading_id, tv.viscosity, tv.measured_date, tv.memo,
               tv.created_by, tv.created_at, tv.updated_by, tv.updated_at
        FROM blend_records br
        LEFT JOIN test_viscosity_readings tv ON tv.blend_record_id = br.id
        WHERE {where_sql}
        {"AND tv.id IS NULL" if unregistered else ""}
        ORDER BY br.work_date DESC, br.id DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    items = []
    for r in rows:
        registered = r["reading_id"] is not None
        items.append({
            "id": int(r["id"]),
            "product_lot": r["product_lot"],
            "product_name": r["product_name"],
            "work_date": r["work_date"],
            "worker": r["worker"],
            "base_recipe_id": r["base_recipe_id"],
            "base_recipe_name": r["base_recipe_name"],
            "registered": registered,
            "reading_id": int(r["reading_id"]) if registered else None,
            "viscosity": float(r["viscosity"]) if registered else None,
            "measured_date": r["measured_date"],
            "memo": r["memo"],
            "created_by": r["created_by"],
            "created_at": r["created_at"],
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"],
        })
    return {
        "items": items,
        "total": int(total),
        "unregistered_total": int(unregistered_total),
        "limit": limit,
    }


def add_test_reading(
    connection: sqlite3.Connection,
    *,
    blend_record_id: int,
    viscosity: float,
    measured_date: str | None,
    memo: str | None,
    created_by: str | None,
    created_at: str,
) -> dict[str, Any]:
    """완료된 시험 배합 기록에 점도 한 건을 등록한다. 커밋은 호출자 책임.

    기록이 없으면 404, 시험이 아니거나 완료가 아니면 400, 이미 값이 있으면 409 로
    TestViscosityError 를 던진다. 측정일이 비면 로컬 '오늘'이다. 반환: 등록한 행
    (get_test_reading 모양, product_lot 포함).
    """
    if not _test_viscosity_ready(connection):
        raise TestViscosityError("시험 점도를 저장할 준비가 되지 않았습니다.", 400)
    record = connection.execute(
        "SELECT id, product_lot, status, COALESCE(is_test, 0) AS is_test "
        "FROM blend_records WHERE id = ?",
        (int(blend_record_id),),
    ).fetchone()
    if record is None:
        raise TestViscosityError("배합 기록을 찾을 수 없습니다.", 404)
    if not record["is_test"]:
        raise TestViscosityError("정식 LOT은 측정 등록 탭에서 등록하세요.", 400)
    if record["status"] != "completed":
        raise TestViscosityError("취소된 시험 기록에는 점도를 등록할 수 없습니다.", 400)
    lot = str(record["product_lot"])
    if get_test_reading(connection, int(blend_record_id)) is not None:
        raise TestViscosityError(f"이미 점도가 등록된 LOT입니다: {lot}", 409)
    resolved_date = (measured_date or "").strip() or date.today().isoformat()
    try:
        cur = connection.execute(
            "INSERT INTO test_viscosity_readings "
            "(blend_record_id, viscosity, measured_date, memo, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                int(blend_record_id),
                float(viscosity),
                resolved_date,
                (memo or "").strip() or None,
                created_by,
                created_at,
            ),
        )
    except sqlite3.IntegrityError as exc:
        # 확인과 저장 사이에 다른 창이 먼저 등록한 경우(blend_record_id UNIQUE).
        raise TestViscosityError(f"이미 점도가 등록된 LOT입니다: {lot}", 409) from exc
    return get_test_reading(connection, int(blend_record_id)) or {
        "id": int(cur.lastrowid),
        "blend_record_id": int(blend_record_id),
        "product_lot": lot,
    }


def correct_test_reading(
    connection: sqlite3.Connection,
    blend_record_id: int,
    *,
    viscosity: float,
    updated_by: str | None,
    updated_at: str,
) -> dict[str, Any] | None:
    """시험 점도 값을 정정한다. 값이 없으면 None. 권한·사유 판정은 라우트 몫.

    반환: {"old", "new", "changed"}. 같은 값이면 쓰지 않는다(changed False).
    """
    reading = get_test_reading(connection, blend_record_id)
    if reading is None:
        return None
    old_value = float(reading["viscosity"])
    new_value = float(viscosity)
    if old_value == new_value:
        return {"old": old_value, "new": new_value, "changed": False}
    connection.execute(
        "UPDATE test_viscosity_readings SET viscosity = ?, updated_by = ?, updated_at = ? "
        "WHERE blend_record_id = ?",
        (new_value, updated_by, updated_at, int(blend_record_id)),
    )
    return {"old": old_value, "new": new_value, "changed": True}


def delete_test_reading(
    connection: sqlite3.Connection, blend_record_id: int
) -> dict[str, Any] | None:
    """시험 점도를 지운다. 지운 행을 돌려준다(없으면 None). 커밋은 호출자 책임."""
    reading = get_test_reading(connection, blend_record_id)
    if reading is None:
        return None
    connection.execute(
        "DELETE FROM test_viscosity_readings WHERE blend_record_id = ?",
        (int(blend_record_id),),
    )
    return reading
