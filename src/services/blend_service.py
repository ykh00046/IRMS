"""배합 실적(잉크 계량 재구축) 서비스 — DHR Generator 이식.

IRMS 레시피(절대중량 g)를 비율(%)로 환산해 임의 배치 총량에 맞는 이론 계량량을
산출하고, 작업자가 실제 계량량·자재 LOT·작업자·저울을 입력해 배합 실적(blend_record)
으로 저장한다. product_lot 은 {제품명}{YYMMDD}{순번:02d} 로 자동 생성.

Design: docs/02-design/features/blend-overhaul.design.md
원본:  C:/X/Program-estimation/v3 (models/data_manager.py, lot_utils.py, excel_exporter.py)

NOTE: 1차 증분은 기록 중심이다. 자동 재고 차감은 기존 계량(weighing)과의 이중 차감을
방지하기 위해 후속 단계에서 통합한다.
"""

import sqlite3
from typing import Any

from .recipe_helpers import SUPERSEDED_RECIPE_IDS_SQL, resolve_chain_tip


# ── 비율/이론량 환산 ────────────────────────────────────────────
def compute_ratios(weights: list[float]) -> list[float]:
    """절대중량 리스트 → 비율(%) 리스트. 합이 0이면 모두 0."""
    total = sum(w or 0 for w in weights)
    if total <= 0:
        return [0.0 for _ in weights]
    return [round((w or 0) / total * 100, 4) for w in weights]


def scale_theory(weights: list[float], total_amount: float) -> list[float]:
    """레시피 절대중량을 배치 총량에 맞춰 비례 배분한 이론 계량량."""
    base_total = sum(w or 0 for w in weights)
    if base_total <= 0:
        return [0.0 for _ in weights]
    return [round((w or 0) / base_total * total_amount, 3) for w in weights]


# ── 레시피 → 배합 입력용 환산 ──────────────────────────────────
def _resolve_latest_revision(connection: sqlite3.Connection, recipe_id: int) -> int:
    """개정 체인을 끝까지 따라가 최신 레시피 id 반환.

    배합 화면이 오래 열려 있으면(목록은 페이지 로드 때 1회) 개정 전의 옛 id 로
    요청이 올 수 있다 — 서버가 항상 최신 개정판으로 귀결시켜 수정 미반영을 막는다.

    판정은 목록(tip)과 같은 규칙을 쓴다 — 옛 구현은 직계 자식만 따라가서 중간
    개정본이 취소되면 그 앞에서 멈췄고(목록은 최신본을 계속 노출), 같은 제품이
    서로 다른 배합 기준으로 저장됐다(감사 F-4). [[recipe_helpers.resolve_chain_tip]]
    """
    return resolve_chain_tip(connection, recipe_id)


def get_recipe_for_blend(
    connection: sqlite3.Connection, recipe_id: int, total_amount: float | None = None
) -> dict[str, Any] | None:
    """레시피와 자재 목록을 비율·이론량과 함께 반환 (배합 입력 화면용).

    total_amount 미지정 시 레시피 절대중량 합계를 기본 배치 총량으로 사용.
    개정된 레시피 id 가 오면 최신 개정판으로 자동 귀결.
    """
    recipe_id = _resolve_latest_revision(connection, recipe_id)
    recipe = connection.execute(
        "SELECT id, product_name, position, ink_name, status, "
        "       base_total AS base_total_setting, base_totals AS base_totals_setting "
        "FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if not recipe:
        return None

    # 기준 자재(anchor_material_id) — 없는 구버전/테스트 DB 도 대응(try/except 폴백).
    # 배합 화면은 이 자재를 먼저 계량하고, 그 실측 중량으로 다른 자재들의 이론량을 산출한다.
    anchor_material_id: int | None = None
    try:
        row = connection.execute(
            "SELECT anchor_material_id FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        if row is not None and row["anchor_material_id"] is not None:
            anchor_material_id = int(row["anchor_material_id"])
    except sqlite3.OperationalError:  # anchor_material_id 컬럼이 없는 구버전/테스트 DB
        anchor_material_id = None

    # 자재코드(material_code)는 진짜 ERP 품목코드(m.code)를 쓴다(P4). 구버전 DB 처럼
    # code 컬럼이 없으면 category 로 폴백(기존 동작 보존) — try/except 2단 쿼리.
    try:
        rows = connection.execute(
            """
            SELECT ri.id AS recipe_item_id, ri.material_id, ri.value_weight, ri.value_text,
                   m.name AS material_name, m.code AS material_code, m.unit AS unit
            FROM recipe_items ri
            JOIN materials m ON m.id = ri.material_id
            WHERE ri.recipe_id = ?
            ORDER BY ri.id
            """,
            (recipe_id,),
        ).fetchall()
    except sqlite3.OperationalError:  # materials.code 컬럼이 없는 구버전/테스트 DB
        rows = connection.execute(
            """
            SELECT ri.id AS recipe_item_id, ri.material_id, ri.value_weight, ri.value_text,
                   m.name AS material_name, m.category AS material_code, m.unit AS unit
            FROM recipe_items ri
            JOIN materials m ON m.id = ri.material_id
            WHERE ri.recipe_id = ?
            ORDER BY ri.id
            """,
            (recipe_id,),
        ).fetchall()

    # 공정 설명 줄(자재 사이 안내문) — 화면 표시 전용, 계산·집계와 무관
    try:
        step_rows = connection.execute(
            "SELECT position, note FROM recipe_steps WHERE recipe_id = ? ORDER BY position, id",
            (recipe_id,),
        ).fetchall()
        steps = [{"position": int(s["position"]), "note": s["note"]} for s in step_rows]
    except sqlite3.OperationalError:  # 테이블 없는 구버전/테스트 DB
        steps = []

    weights = [float(r["value_weight"] or 0) for r in rows]
    base_total = sum(weights)
    total = float(total_amount) if total_amount and total_amount > 0 else base_total
    ratios = compute_ratios(weights)
    theory = scale_theory(weights, total)

    # 방어: 기준 자재가 지정돼 있어도 (1) 해당 자재가 항목에 없거나 (2) 그 자재의
    # 기준 중량(value_weight)이 0 이하면 기준으로 쓸 수 없다 — anchor 를 무효(None) 처리.
    effective_anchor = anchor_material_id
    if effective_anchor is not None:
        anchor_idx = next(
            (idx for idx, r in enumerate(rows) if int(r["material_id"]) == effective_anchor),
            None,
        )
        if anchor_idx is None or weights[anchor_idx] <= 0:
            effective_anchor = None

    items = []
    for idx, r in enumerate(rows):
        items.append({
            "recipe_item_id": int(r["recipe_item_id"]),
            "material_id": int(r["material_id"]),
            "material_name": r["material_name"],
            "material_code": r["material_code"],
            "unit": r["unit"],
            "value_weight": weights[idx],
            "ratio": ratios[idx],
            "theory_amount": theory[idx],
            "sequence_order": idx + 1,
            # 기준 자재 여부 — 배합 시 이 자재의 실측값으로 다른 자재 이론량을 산출.
            "is_anchor": effective_anchor is not None
            and int(r["material_id"]) == effective_anchor,
        })
    # 기준 배합량(최대 3개): 레시피 관리에서 지정한 레시피만 값 반환(버튼 노출).
    # base_totals(CSV) 우선, 없으면 (구) 단일 base_total 폴백 — 미지정은 빈 목록.
    default_totals: list[float] = []
    raw_totals = recipe["base_totals_setting"]
    if raw_totals:
        for token in str(raw_totals).split(","):
            token = token.strip()
            try:
                value = float(token)
            except ValueError:
                continue
            if value > 0 and value not in default_totals:
                default_totals.append(value)
    elif recipe["base_total_setting"] and float(recipe["base_total_setting"]) > 0:
        default_totals = [float(recipe["base_total_setting"])]
    default_totals = [round(v, 3) for v in default_totals[:3]]
    return {
        "recipe": {
            "id": int(recipe["id"]),
            "product_name": recipe["product_name"],
            "position": recipe["position"],
            "ink_name": recipe["ink_name"],
            "status": recipe["status"],
            "use_reactor": product_uses_reactor(connection, recipe["product_name"]),
            # 기준 자재(방어 처리 후). None 이면 total_amount 기준 기존 동작.
            "anchor_material_id": effective_anchor,
            # 레시피별 허용 편차(EFFECTIVE). tolerance_g 미지정/무효면 기본값 0.05g.
            "tolerance_g": recipe_tolerance_g(connection, int(recipe["id"])),
        },
        "base_total": round(base_total, 3),
        "steps": steps,
        "default_totals": default_totals,
        # (구) 단일 필드 — 하위호환(첫 값 또는 None)
        "default_total": default_totals[0] if default_totals else None,
        "total_amount": round(total, 3),
        "items": items,
    }


def _material_code_map(connection: sqlite3.Connection) -> dict[str, str]:
    """자재명 → materials.code(ERP 품목코드) 매핑. P4 최우선 ERP 코드 출처.

    구버전 DB(materials.code 컬럼 없음)는 빈 맵 폴백 — recipe_tolerance_g 의
    OperationalError 방어 패턴과 동일. NULL 코드는 제외(미부여=빈 값).
    """
    try:
        rows = connection.execute(
            "SELECT name, code FROM materials WHERE code IS NOT NULL AND code <> ''"
        ).fetchall()
    except sqlite3.OperationalError:  # code 컬럼이 없는 구버전/테스트 DB
        return {}
    return {(r["name"] or "").strip(): (r["code"] or "").strip() for r in rows if r["code"]}


def _erp_code_map(connection: sqlite3.Connection) -> dict[str, str]:
    """자재명 → RM 별칭(레거시 ERP 코드) 매핑.

    RM… 코드는 레거시 이관이 material_aliases 에 별칭으로 넣은 것이므로 거기서 찾는다.
    RM 으로 시작하는 별칭 우선. 별칭은 materials.code 다음 우선순위(자세한 사항은
    _resolve_erp_code).
    """
    try:
        rows = connection.execute(
            "SELECT m.name AS name, a.alias_name AS alias "
            "FROM material_aliases a JOIN materials m ON m.id = a.material_id"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    mapping: dict[str, str] = {}
    for r in rows:
        alias = (r["alias"] or "").strip()
        if not alias:
            continue
        current = mapping.get(r["name"])
        if current is None or (
            alias.upper().startswith("RM") and not current.upper().startswith("RM")
        ):
            mapping[r["name"]] = alias
    return mapping


def _resolve_erp_code(
    name: str,
    code: str,
    alias_map: dict[str, str],
    material_code_map: dict[str, str] | None = None,
) -> str:
    """ERP 품목코드 결정. 우선순위(P4):
    materials.code > RM 별칭 > RM 형태 저장 코드 > RM 형태 자재명 > 별칭(비RM).

    materials.code 가 도입되기 전 화면 '자재코드'는 materials.category(분류) 였고,
    이 인자 `code` 는 그 legacy 값을 받는다 — RM 형태인 경우에만 후보로 쓴다.
    """
    # 1) materials.code — 정식 ERP 품목코드(P4 최우선)
    if material_code_map is not None:
        mc = material_code_map.get(name, "")
        if mc:
            return mc
    # 2) RM 별칭
    alias = alias_map.get(name, "")
    if alias.upper().startswith("RM"):
        return alias
    # 3) RM 형태의 저장 코드(category 등 legacy)
    if code.upper().startswith("RM"):
        return code
    # 4) RM 형태의 자재명
    if name.upper().startswith("RM"):
        return name
    # 5) 비RM 별칭이라도 있으면 제공(빈 행 skip 회피)
    return alias


def material_usage_periods(
    connection: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    group: str = "total",
    by_product: bool = False,
) -> dict[str, Any]:
    """자재 사용량(불출) 기간 집계 — 외부 재고 대시보드 연동용([[roadmap-2026H2]] P3).

    group: total(기간 합계, 기본) | day(작업일별) | month(월별).
    by_product=True 면 제품(product_name) 차원 추가 — 자재별 '주 사용처(제품)' 분석용.
    erp_code(RM 품목코드 — 재고 시스템 매칭 키)·material_code 포함, 단위 g 고정.
    """
    period_expr = {
        "total": "NULL",
        "day": "br.work_date",
        "month": "substr(br.work_date, 1, 7)",
    }[group]
    # 주의: GROUP BY 에 별칭 'product_name' 을 쓰면 SQLite 가 br.product_name
    # 실컬럼으로 해석해 by_product=False 에서도 제품별로 쪼개진다 — 조건부 구성.
    product_select = "br.product_name AS product_name," if by_product else ""
    product_group = ", br.product_name" if by_product else ""
    rows = connection.execute(
        f"""
        SELECT {period_expr} AS period,
               {product_select}
               COALESCE(bd.material_code, '') AS material_code,
               bd.material_name AS material_name,
               COALESCE(SUM(bd.actual_amount), 0) AS total_actual,
               COALESCE(SUM(bd.theory_amount), 0) AS total_theory,
               COUNT(DISTINCT bd.blend_record_id) AS batch_count
        FROM blend_details bd
        JOIN blend_records br ON br.id = bd.blend_record_id
        WHERE br.status = 'completed' AND br.work_date >= ? AND br.work_date <= ?
        GROUP BY {period_expr}{product_group}, bd.material_code, bd.material_name
        ORDER BY period, total_actual DESC
        """,
        (start_date, end_date),
    ).fetchall()
    alias_map = _erp_code_map(connection)
    material_code_map = _material_code_map(connection)
    items = [
        {
            "period": r["period"],
            **({"product_name": r["product_name"]} if by_product else {}),
            "erp_code": _resolve_erp_code(
                r["material_name"], r["material_code"], alias_map, material_code_map
            ),
            "material_code": r["material_code"],
            "material_name": r["material_name"],
            "total_actual": round(float(r["total_actual"]), 3),
            "total_theory": round(float(r["total_theory"]), 3),
            "batch_count": int(r["batch_count"]),
        }
        for r in rows
    ]
    rec_count = connection.execute(
        "SELECT COUNT(*) FROM blend_records br "
        "WHERE br.status = 'completed' AND br.work_date >= ? AND br.work_date <= ?",
        (start_date, end_date),
    ).fetchone()[0]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "group": group,
        "unit": "g",
        "record_count": int(rec_count),
        "total_weight": round(sum(i["total_actual"] for i in items), 3),
        "items": items,
    }


def material_usage(
    connection: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """배합 기록 기반 자재 사용 분석. 기간 내 완료 기록의 자재별 실제/이론 사용량·건수."""
    where = ["br.status = 'completed'"]
    params: list[Any] = []
    if start_date:
        where.append("br.work_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("br.work_date <= ?")
        params.append(end_date)
    wsql = " AND ".join(where)
    rows = connection.execute(
        f"""
        SELECT bd.material_name AS material_name,
               COALESCE(SUM(bd.actual_amount), 0) AS total_actual,
               COALESCE(SUM(bd.theory_amount), 0) AS total_theory,
               COUNT(DISTINCT bd.blend_record_id) AS usage_count
        FROM blend_details bd
        JOIN blend_records br ON br.id = bd.blend_record_id
        WHERE {wsql}
        GROUP BY bd.material_name
        ORDER BY total_actual DESC
        """,
        params,
    ).fetchall()
    items = [
        {
            "material_name": r["material_name"],
            "total_actual": round(float(r["total_actual"]), 3),
            "total_theory": round(float(r["total_theory"]), 3),
            "usage_count": int(r["usage_count"]),
        }
        for r in rows
    ]
    rec_count = connection.execute(
        f"SELECT COUNT(*) FROM blend_records br WHERE {wsql}", params
    ).fetchone()[0]
    total_weight = round(sum(i["total_actual"] for i in items), 3)
    return {
        "items": items,
        "record_count": int(rec_count),
        "total_weight": total_weight,
        "material_count": len(items),
    }


def product_usage(
    connection: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """제품별 배합 빈도 분석. 기간 내 완료 기록의 제품별 배치 수·총 배합량·최근 작업일."""
    where = ["status = 'completed'"]
    params: list[Any] = []
    if start_date:
        where.append("work_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("work_date <= ?")
        params.append(end_date)
    wsql = " AND ".join(where)
    rows = connection.execute(
        f"""
        SELECT product_name,
               COUNT(*) AS batch_count,
               COALESCE(SUM(total_amount), 0) AS total_amount,
               MAX(work_date) AS last_work_date
        FROM blend_records
        WHERE {wsql}
        GROUP BY product_name
        ORDER BY batch_count DESC, product_name ASC
        """,
        params,
    ).fetchall()
    items = [
        {
            "product_name": r["product_name"],
            "batch_count": int(r["batch_count"]),
            "total_amount": round(float(r["total_amount"]), 3),
            "last_work_date": r["last_work_date"],
        }
        for r in rows
    ]
    return {
        "items": items,
        "product_count": len(items),
        "batch_total": sum(i["batch_count"] for i in items),
        "last_work_date": max((i["last_work_date"] for i in items), default=None),
    }


def batch_details(
    connection: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
    product: str | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    """배치 상세 — 완료 기록의 자재별 비율·이론량·실제량·편차 평면 목록(작업일 역순)."""
    where = ["br.status = 'completed'"]
    params: list[Any] = []
    if start_date:
        where.append("br.work_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("br.work_date <= ?")
        params.append(end_date)
    if product:
        where.append("br.product_name = ?")
        params.append(product)
    wsql = " AND ".join(where)
    rows = connection.execute(
        f"""
        SELECT br.id AS record_id, br.work_date, br.product_lot, br.product_name, br.worker,
               bd.material_code, bd.material_name, bd.material_lot,
               bd.ratio, bd.theory_amount, bd.actual_amount
        FROM blend_details bd
        JOIN blend_records br ON br.id = bd.blend_record_id
        WHERE {wsql}
        ORDER BY br.work_date DESC, br.id DESC, bd.sequence_order ASC
        LIMIT ?
        """,
        [*params, max(1, min(int(limit), 10000))],
    ).fetchall()
    items = []
    for r in rows:
        theory = None if r["theory_amount"] is None else float(r["theory_amount"])
        actual = None if r["actual_amount"] is None else float(r["actual_amount"])
        variance = (
            None if theory is None or actual is None else round(actual - theory, 3)
        )
        items.append(
            {
                "record_id": int(r["record_id"]),
                "work_date": r["work_date"],
                "product_lot": r["product_lot"],
                "product_name": r["product_name"],
                "worker": r["worker"],
                "material_code": r["material_code"],
                "material_name": r["material_name"],
                "material_lot": r["material_lot"],
                "ratio": None if r["ratio"] is None else float(r["ratio"]),
                "theory_amount": theory,
                "actual_amount": actual,
                "variance": variance,
            }
        )
    return {
        "items": items,
        "total": len(items),
        "batch_count": len({i["record_id"] for i in items}),
        "material_count": len({i["material_name"] for i in items}),
    }


def trace_material_lot(
    connection: sqlite3.Connection,
    lot: str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """자재 LOT 역추적 — 이 LOT 이 투입된 배합 기록을 최신 작업일 순으로 반환.

    부분 일치(LIKE %lot%) — 현장에서 접두/접미만 기억하는 경우 대응. 취소 기록도
    포함하되 status 를 함께 반환해 화면에서 구분한다(리콜 추적은 누락이 더 위험).
    사용자 입력의 %/_ 는 리터럴로 이스케이프(generate_product_lot 과 동일 패턴).
    """
    clean = str(lot).strip()
    escaped = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = connection.execute(
        """
        SELECT r.id AS record_id, r.product_lot, r.product_name, r.work_date,
               r.worker, r.status, r.total_amount,
               d.material_name, d.material_code, d.material_lot,
               d.actual_amount, d.theory_amount
        FROM blend_details d
        JOIN blend_records r ON r.id = d.blend_record_id
        WHERE d.material_lot LIKE ? ESCAPE '\\'
        ORDER BY r.work_date DESC, r.id DESC, d.sequence_order
        LIMIT ?
        """,
        (f"%{escaped}%", int(limit)),
    ).fetchall()
    items = [dict(r) for r in rows]
    return {
        "lot": clean,
        "items": items,
        "total": len(items),
        "record_count": len({it["record_id"] for it in items}),
    }


def list_blend_recipes(connection: sqlite3.Connection, *, dhr: bool = False) -> list[dict[str, Any]]:
    """배합에 쓸 수 있는 레시피 목록 (취소/초안 제외).

    dhr=False(기본): 일반 레시피. dhr=True: DHR 전용 레시피(일괄 배합일지 생성용).
    """
    rows = connection.execute(
        """
        SELECT r.id, r.product_name, r.position, r.ink_name, r.status, r.category,
               r.product_code,
               COUNT(ri.id) AS item_count,
               COALESCE(SUM(ri.value_weight), 0) AS total_weight
        FROM recipes r
        LEFT JOIN recipe_items ri ON ri.recipe_id = r.id
        WHERE r.status NOT IN ('canceled', 'draft')
          AND COALESCE(r.is_dhr, 0) = ?
          AND r.id NOT IN (""" + SUPERSEDED_RECIPE_IDS_SQL + """)
        GROUP BY r.id
        HAVING item_count > 0
        ORDER BY r.created_at DESC, r.id DESC
        """,
        (1 if dhr else 0,),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "product_name": r["product_name"],
            "position": r["position"],
            "ink_name": r["ink_name"],
            "status": r["status"],
            "category": r["category"],
            # 반제품 품목코드(item-code P1). 매칭(P2) 또는 등록(P3)으로 부여.
            # UI 는 P6 범위 밖이므로 응답 필드만 노출.
            "product_code": r["product_code"],
            "item_count": int(r["item_count"]),
            "total_weight": round(float(r["total_weight"]), 3),
        }
        for r in rows
    ]


# ── product_lot 생성 ────────────────────────────────────────────
def generate_product_lot(
    connection: sqlite3.Connection, product_name: str, work_date: str
) -> str:
    """{제품명}{YYMMDD}{순번:02d}. 같은 날 같은 제품의 기존 최대 순번+1."""
    digits = "".join(ch for ch in work_date if ch.isdigit())
    yymmdd = digits[2:8] if len(digits) >= 8 else digits[-6:]
    base = f"{product_name.strip()}{yymmdd}"
    rows = connection.execute(
        "SELECT product_lot FROM blend_records WHERE product_lot LIKE ? ESCAPE '\\'",
        (base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
    ).fetchall()
    max_seq = 0
    for r in rows:
        suffix = str(r["product_lot"])[len(base):]
        if suffix.isdigit():
            max_seq = max(max_seq, int(suffix))
    return f"{base}{max_seq + 1:02d}"


# 자재별 계량 허용 편차(g). 저울(A&D GX-10202M) 실측값 연동 기준 —
# 각 자재는 |실제-이론| ≤ 0.05g 이어야 하고, 배치 합계 편차는 제한하지 않는다.
# 레시피별 허용 편차가 도입되었어도 이 값은 기본값(DEFAULT) 으로 남는다 — 다른 모듈이
# WEIGHING_TOLERANCE_G 를 import 하므로 이름은 보존한다.
WEIGHING_TOLERANCE_G = 0.05
DEFAULT_WEIGHING_TOLERANCE_G = WEIGHING_TOLERANCE_G  # 동일 기본값의 가독용 별칭


def recipe_tolerance_g(
    connection: sqlite3.Connection, recipe_id: int | None
) -> float:
    """레시피의 유효 허용 편차(g). recipe_id 가 None 이거나, 레시피에 tolerance_g 이
    없거나 0 이하이면 기본값(0.05g) 반환. 구버전(컬럼 없음) DB 도 방어적 폴백.
    """
    if recipe_id is None:
        return WEIGHING_TOLERANCE_G
    try:
        row = connection.execute(
            "SELECT tolerance_g FROM recipes WHERE id = ?", (int(recipe_id),)
        ).fetchone()
    except sqlite3.OperationalError:  # tolerance_g 컬럼이 없는 구버전/테스트 DB
        return WEIGHING_TOLERANCE_G
    if row is None:
        return WEIGHING_TOLERANCE_G
    try:
        value = float(row["tolerance_g"]) if row["tolerance_g"] is not None else None
    except (TypeError, ValueError):
        return WEIGHING_TOLERANCE_G
    if value is None or value <= 0:
        return WEIGHING_TOLERANCE_G
    return value


class RecipeRevisedError(Exception):
    """배합 화면이 들고 있던 레시피가 그 사이 개정됐다 — 옛 배합비로 저장하면 안 된다."""


class RecipeMismatchError(Exception):
    """저장 요청의 자재 구성이 레시피와 다르다."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def derive_details_from_recipe(
    connection: sqlite3.Connection,
    recipe_id: int,
    total_amount: float,
    details: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    """비율·이론량을 **서버가 레시피에서 직접 산출**해 상세를 재구성한다 (감사 F-5).

    클라이언트가 보낸 ratio·theory_amount 는 신뢰하지 않고 버린다 — 화면이 오래 열려
    있었거나 조작됐으면 옛/거짓 배합비가 규제 문서(DHR)에 그대로 실린다. 사람만 알 수
    있는 값(실제 계량량·자재 LOT·수동입력 여부)만 클라이언트에서 받는다.

    '비교 후 거부'가 아니라 '서버가 산출'인 이유: 반올림·기준자재 파생 때문에 정상
    저장이 오판으로 막히면 현장이 멈춘다. 비교할 값이 없으면 오판도 없다.

    기준 자재(anchor) 레시피는 총량이 실측에서 파생된다 — 기준 자재의 실제 계량량으로
    총량을 되돌려 계산하고, 나머지 이론량을 그 총량에 비례 배분한다. 기준 행의 이론량은
    실측값 자신이므로 편차 0 (기존 화면 규칙과 동일).

    개정 여부는 호출부가 먼저 검사한다(RecipeRevisedError).
    """
    recipe = get_recipe_for_blend(connection, recipe_id, total_amount)
    if not recipe:
        raise RecipeMismatchError("레시피를 찾을 수 없습니다.")

    items = {str(it["material_name"]): it for it in recipe["items"]}
    incoming = {str(d.get("material_name") or ""): d for d in details}
    if set(items) != set(incoming):
        missing = sorted(set(items) - set(incoming))
        extra = sorted(set(incoming) - set(items))
        parts = []
        if missing:
            parts.append("누락: " + ", ".join(missing))
        if extra:
            parts.append("레시피에 없음: " + ", ".join(extra))
        raise RecipeMismatchError(
            "자재 구성이 레시피와 다릅니다 — 화면을 새로고침하세요. (" + " / ".join(parts) + ")"
        )

    # 기준 자재가 있으면 총량을 실측에서 되돌려 계산 (없으면 작업자가 고른 배치 총량 사용)
    total = float(total_amount)
    anchor = next((it for it in recipe["items"] if it.get("is_anchor")), None)
    if anchor is not None:
        anchor_actual = _opt_num(incoming[str(anchor["material_name"])].get("actual_amount"))
        if anchor_actual is None or anchor_actual <= 0:
            raise RecipeMismatchError(
                f"기준 자재({anchor['material_name']})를 먼저 계량하세요."
            )
        ratio = float(anchor["ratio"] or 0)
        if ratio <= 0:
            raise RecipeMismatchError("기준 자재의 레시피 비율이 0 입니다.")
        total = round(anchor_actual * 100.0 / ratio, 3)

    derived: list[dict[str, Any]] = []
    for order, item in enumerate(recipe["items"], start=1):
        name = str(item["material_name"])
        sent = incoming[name]
        ratio = float(item["ratio"] or 0)
        if anchor is not None:
            theory = anchor_actual if item.get("is_anchor") else round(total * ratio / 100.0, 3)
        else:
            theory = float(item["theory_amount"] or 0)
        derived.append({
            "material_id": item.get("material_id"),
            "material_code": item.get("material_code"),
            "material_name": name,
            "material_lot": sent.get("material_lot"),        # 사람만 아는 값
            "actual_amount": _opt_num(sent.get("actual_amount")),
            "manual_entry": bool(sent.get("manual_entry")),
            "ratio": ratio,                                   # ← 서버 산출
            "theory_amount": theory,                          # ← 서버 산출
            "sequence_order": order,
        })
    return derived, total


def weighing_tolerance_violations(
    details: list[dict[str, Any]], tolerance_g: float | None = None
) -> list[str]:
    """허용 편차를 넘는 자재명 목록. 실제량 미입력(None)은 검사 제외.

    tolerance_g 미지정(None) 시 기본값(0.05g) 사용 — 단일 인수 호출은 기존 동작 보존.
    """
    tol = WEIGHING_TOLERANCE_G if tolerance_g is None else float(tolerance_g)
    offenders: list[str] = []
    for d in details:
        theory = _opt_num(d.get("theory_amount"))
        actual = _opt_num(d.get("actual_amount"))
        if theory is None or actual is None:
            continue
        if abs(actual - theory) > tol + 1e-9:
            offenders.append(str(d.get("material_name") or "?"))
    return offenders


def product_uses_reactor(connection: sqlite3.Connection, product_name: str) -> bool:
    """제품명(레시피명)에 해당하는 점도 반제품이 반응기 진행(use_reactor)인지.

    반응기는 배합 실적을 진행한 위치이고, 반응기 사용 여부는 점도 반제품 설정
    (viscosity_products.use_reactor)이 소유한다. 실적 저장 시 이 값으로 반응기
    지정을 강제할지 판단한다.
    """
    name = str(product_name or "").strip()
    if not name:
        return False
    row = connection.execute(
        "SELECT use_reactor FROM viscosity_products "
        "WHERE code = ? OR name = ? ORDER BY use_reactor DESC LIMIT 1",
        (name, name),
    ).fetchone()
    return bool(row["use_reactor"]) if row else False


# ── 배합 기록 생성/조회 ─────────────────────────────────────────
def create_blend_record(
    connection: sqlite3.Connection,
    *,
    recipe_id: int | None,
    product_name: str,
    ink_name: str | None,
    position: str | None,
    worker: str,
    work_date: str,
    work_time: str | None,
    total_amount: float,
    scale: str | None,
    note: str | None,
    details: list[dict[str, Any]],
    created_by: str | None,
    created_at: str,
    worker_sign: str | None = None,
    reactor: int | None = None,
    manual_entry: bool = False,
) -> int:
    """배합 실적 1건 저장 (헤더 + 상세). product_lot 자동 생성.

    reactor 지정 시 실적을 진행한 반응기(1~4)를 기록한다(반응기 진행 반제품).
    manual_entry=True 면 저울 연동 중 수동 입력으로 계량됐음을 기록한다(추적성).
    """
    # 감사 F-1: 채번+INSERT 원자화. 쓰기 락을 선획득(BEGIN IMMEDIATE)해 동시 요청의
    # 채번을 직렬화한다(WAL 에서 리더는 라이터를 막지 않으므로 명시 락이 필요).
    # 이미 트랜잭션 안이면(create_bulk 루프의 2번째 이후 호출 등) 그대로 진행.
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    # UNIQUE(product_lot) 위반 시 재채번 재시도 — BEGIN IMMEDIATE 하에서는 사실상
    # 발생하지 않지만(단일 라이터), 교차 프로세스 등 방어적 재시도를 둔다.
    last_error: sqlite3.IntegrityError | None = None
    cur = None
    for _attempt in range(3):
        product_lot = generate_product_lot(connection, product_name, work_date)
        try:
            cur = connection.execute(
                """
                INSERT INTO blend_records
                    (product_lot, recipe_id, product_name, ink_name, position, worker,
                     work_date, work_time, total_amount, scale, status, note,
                     worker_sign, reactor, manual_entry, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_lot, recipe_id, product_name.strip(), ink_name, position, worker.strip(),
                    work_date, work_time, float(total_amount), scale,
                    (note or "").strip() or None, worker_sign,
                    int(reactor) if reactor is not None else None,
                    1 if manual_entry else 0,
                    created_by, created_at, created_at,
                ),
            )
            break
        except sqlite3.IntegrityError as exc:
            if "product_lot" not in str(exc):
                raise
            last_error = exc
    else:
        raise last_error  # 3회 모두 위반 — 비정상 상황을 그대로 드러낸다(500)
    record_id = int(cur.lastrowid)

    for idx, d in enumerate(details):
        connection.execute(
            """
            INSERT INTO blend_details
                (blend_record_id, material_id, material_code, material_name,
                 material_lot, ratio, theory_amount, actual_amount, sequence_order,
                 manual_entry, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                d.get("material_id"),
                (d.get("material_code") or None),
                str(d.get("material_name") or "").strip(),
                (str(d.get("material_lot")).strip() if d.get("material_lot") else None),
                _opt_num(d.get("ratio")),
                _opt_num(d.get("theory_amount")),
                _opt_num(d.get("actual_amount")),
                int(d.get("sequence_order") or (idx + 1)),
                1 if d.get("manual_entry") else 0,
                created_at,
            ),
        )
    return record_id


def update_blend_record(
    connection: sqlite3.Connection,
    record_id: int,
    *,
    product_name: str,
    ink_name: str | None,
    position: str | None,
    worker: str,
    work_date: str,
    work_time: str | None,
    total_amount: float,
    scale: str | None,
    note: str | None,
    details: list[dict[str, Any]],
    reactor: int | None,
    updated_at: str,
) -> None:
    """배합 실적 전체 수정(책임자 전용). product_lot·상태·생성정보·서명은 보존하고,
    헤더와 상세(전량 교체)만 갱신한다. 상세는 create 와 동일 규칙으로 다시 채운다.
    """
    connection.execute(
        """
        UPDATE blend_records SET
            product_name = ?, ink_name = ?, position = ?, worker = ?,
            work_date = ?, work_time = ?, total_amount = ?, scale = ?,
            note = ?, reactor = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            product_name.strip(), ink_name, position, worker.strip(),
            work_date, work_time, float(total_amount), scale,
            (note or "").strip() or None,
            int(reactor) if reactor is not None else None,
            updated_at, record_id,
        ),
    )
    connection.execute("DELETE FROM blend_details WHERE blend_record_id = ?", (record_id,))
    for idx, d in enumerate(details):
        connection.execute(
            """
            INSERT INTO blend_details
                (blend_record_id, material_id, material_code, material_name,
                 material_lot, ratio, theory_amount, actual_amount, sequence_order,
                 manual_entry, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                d.get("material_id"),
                (d.get("material_code") or None),
                str(d.get("material_name") or "").strip(),
                (str(d.get("material_lot")).strip() if d.get("material_lot") else None),
                _opt_num(d.get("ratio")),
                _opt_num(d.get("theory_amount")),
                _opt_num(d.get("actual_amount")),
                int(d.get("sequence_order") or (idx + 1)),
                1 if d.get("manual_entry") else 0,
                updated_at,
            ),
        )


def _opt_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def create_bulk(
    connection: sqlite3.Connection,
    *,
    recipe_id: int,
    worker: str,
    scale: str | None,
    entries: list[dict[str, Any]],
    created_by: str | None,
    created_at: str,
    actual_equals_theory: bool = True,
) -> list[int]:
    """같은 레시피로 여러 (작업일, 총량) 배합 실적을 일괄 생성. record_id 리스트 반환.

    각 항목은 레시피 비율로 이론량을 산출하고, actual_equals_theory 면 실제량=이론량으로
    채운다(일괄 계획·문서용). 자재 LOT 은 비움.
    """
    base = get_recipe_for_blend(connection, recipe_id)
    if not base:
        raise ValueError("레시피를 찾을 수 없습니다.")
    recipe = base["recipe"]
    weights = [it["value_weight"] for it in base["items"]]
    ids: list[int] = []
    for entry in entries:
        total = float(entry["total_amount"])
        theory = scale_theory(weights, total)
        details = []
        for idx, it in enumerate(base["items"]):
            th = theory[idx]
            details.append({
                "material_id": it["material_id"],
                "material_name": it["material_name"],
                "material_code": it["material_code"],
                "ratio": it["ratio"],
                "theory_amount": th,
                "actual_amount": th if actual_equals_theory else None,
                "material_lot": None,
                "sequence_order": idx + 1,
            })
        rid = create_blend_record(
            connection,
            recipe_id=recipe_id,
            product_name=recipe["product_name"],
            ink_name=recipe["ink_name"],
            position=recipe["position"],
            worker=worker,
            work_date=entry["work_date"],
            work_time=entry.get("work_time"),
            total_amount=total,
            scale=scale,
            note=entry.get("note"),
            details=details,
            created_by=created_by,
            created_at=created_at,
        )
        ids.append(rid)
    return ids


def create_continuous(
    connection: sqlite3.Connection,
    *,
    recipe_id: int,
    product_name: str,
    ink_name: str | None,
    position: str | None,
    worker: str,
    work_date: str,
    work_time: str | None,
    total_amount: float,
    scale: str | None,
    note: str | None,
    lots_details: list[list[dict[str, Any]]],
    created_by: str | None,
    created_at: str,
    worker_sign: str | None = None,
    reactor: int | None = None,
    lot_totals: list[float | None] | None = None,
) -> list[int]:
    """이미 서버 도출·편차검사를 통과한 로트별 상세를 순차 저장. record_id 리스트 반환.

    create_blend_record 를 로트마다 호출한다. 첫 호출이 BEGIN IMMEDIATE 로 트랜잭션을
    열고 이후 호출은 같은 트랜잭션에서 진행되므로(create_bulk 와 동일), generate_product_lot
    이 직전 로트 INSERT 를 보고 순번({제품명}{YYMMDD}{순번})을 연속 채번한다.

    lot_totals 가 주어지면 해당 로트의 record.total_amount 를 그 값으로 저장한다(초과 계량
    증량). null 원소 또는 lot_totals 미전송이면 공용 total_amount 를 그대로 쓴다(하위호환).
    """
    norm_lot_totals = list(lot_totals) if lot_totals else []
    ids: list[int] = []
    for lot_idx, details in enumerate(lots_details):
        lot_total = (
            norm_lot_totals[lot_idx]
            if lot_idx < len(norm_lot_totals) and norm_lot_totals[lot_idx]
            else total_amount
        )
        rid = create_blend_record(
            connection,
            recipe_id=recipe_id,
            product_name=product_name,
            ink_name=ink_name,
            position=position,
            worker=worker,
            work_date=work_date,
            work_time=work_time,
            total_amount=lot_total,
            scale=scale,
            note=note,
            details=details,
            created_by=created_by,
            created_at=created_at,
            worker_sign=worker_sign,
            reactor=reactor,
            manual_entry=any(bool(d.get("manual_entry")) for d in details),
        )
        ids.append(rid)
    return ids


def get_blend_record(connection: sqlite3.Connection, record_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, product_lot, recipe_id, product_name, ink_name, position, worker,
               work_date, work_time, total_amount, scale, status, note, reactor,
               manual_entry, reviewed_by, reviewed_at, approved_by, approved_at,
               worker_sign, reviewed_sign, approved_sign,
               created_by, created_at, updated_at
        FROM blend_records WHERE id = ?
        """,
        (record_id,),
    ).fetchone()
    if not row:
        return None
    details = connection.execute(
        """
        SELECT id, material_id, material_code, material_name, material_lot,
               ratio, theory_amount, actual_amount, sequence_order, manual_entry
        FROM blend_details
        WHERE blend_record_id = ?
        ORDER BY sequence_order, id
        """,
        (record_id,),
    ).fetchall()
    record = _serialize_record(row)
    record["details"] = [_serialize_detail(d) for d in details]
    record["variance"] = _variance_summary(record["details"])
    return record


def list_blend_records(
    connection: sqlite3.Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    worker: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses = ["status != 'canceled'"]
    params: list[Any] = []
    if start_date:
        clauses.append("work_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("work_date <= ?")
        params.append(end_date)
    if worker:
        clauses.append("worker = ?")
        params.append(worker)
    if search:
        # 자재 LOT 역추적 확장 — 검색어가 어떤 상세 행의 material_lot 에 걸려도
        # 그 배합 기록을 반환(추적성). 동일 검색어 토큰을 LIKE 파라미터로 재사용.
        clauses.append(
            "(product_lot LIKE ? OR product_name LIKE ? OR ink_name LIKE ? "
            "OR EXISTS (SELECT 1 FROM blend_details d "
            "WHERE d.blend_record_id = blend_records.id "
            "AND d.material_lot LIKE ?))"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])
    where = " AND ".join(clauses)
    params.append(int(limit))
    rows = connection.execute(
        f"""
        SELECT id, product_lot, recipe_id, product_name, ink_name, position, worker,
               work_date, work_time, total_amount, scale, status, note, created_at,
               manual_entry
        FROM blend_records
        WHERE {where}
        ORDER BY work_date DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_serialize_record(r) for r in rows]


def list_workers(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT DISTINCT worker FROM blend_records WHERE worker IS NOT NULL ORDER BY worker"
    ).fetchall()
    return [r["worker"] for r in rows if r["worker"]]


def _serialize_record(row: sqlite3.Row) -> dict[str, Any]:
    keys = row.keys()
    out = {
        "id": int(row["id"]),
        "product_lot": row["product_lot"],
        "recipe_id": row["recipe_id"],
        "product_name": row["product_name"],
        "ink_name": row["ink_name"],
        "position": row["position"],
        "worker": row["worker"],
        "work_date": row["work_date"],
        "work_time": row["work_time"],
        "total_amount": float(row["total_amount"]) if row["total_amount"] is not None else None,
        "scale": row["scale"],
        "status": row["status"],
        "note": row["note"],
        "created_at": row["created_at"] if "created_at" in keys else None,
        "manual_entry": bool(row["manual_entry"]) if "manual_entry" in keys else False,
    }
    for f in ("reviewed_by", "reviewed_at", "approved_by", "approved_at",
              "worker_sign", "reviewed_sign", "approved_sign", "reactor"):
        out[f] = row[f] if f in keys else None
    return out


def _serialize_detail(row: sqlite3.Row) -> dict[str, Any]:
    theory = row["theory_amount"]
    actual = row["actual_amount"]
    variance = None
    variance_pct = None
    if theory is not None and actual is not None:
        variance = round(actual - theory, 3)
        if theory:
            variance_pct = round((actual - theory) / theory * 100, 2)
    return {
        "id": int(row["id"]),
        "material_id": row["material_id"],
        "material_code": row["material_code"],
        "material_name": row["material_name"],
        "material_lot": row["material_lot"],
        "ratio": row["ratio"],
        "theory_amount": theory,
        "actual_amount": actual,
        "variance": variance,
        "variance_pct": variance_pct,
        "sequence_order": int(row["sequence_order"]),
        "manual_entry": bool(row["manual_entry"]) if "manual_entry" in row.keys() else False,
    }


def _variance_summary(details: list[dict[str, Any]]) -> dict[str, Any]:
    theory_total = sum(d["theory_amount"] or 0 for d in details)
    actual_total = sum(d["actual_amount"] or 0 for d in details)
    abs_var = sum(abs(d["variance"]) for d in details if d["variance"] is not None)
    return {
        "theory_total": round(theory_total, 3),
        "actual_total": round(actual_total, 3),
        "net_variance": round(actual_total - theory_total, 3),
        "abs_variance": round(abs_var, 3),
    }
