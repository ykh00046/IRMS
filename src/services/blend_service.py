"""배합 실적(잉크 계량 재구축) 서비스 — DHR Generator 이식.

IRMS 레시피(절대중량 g)를 비율(%)로 환산해 임의 배치 총량에 맞는 이론 계량량을
산출하고, 작업자가 실제 계량량·자재 LOT·작업자·저울을 입력해 배합 실적(blend_record)
으로 저장한다. product_lot 은 {제품명}{YYMMDD}{순번:02d} 로 자동 생성.

Design: docs/02-design/features/blend-overhaul.design.md
원본:  C:/X/Program-estimation/v3 (models/data_manager.py, lot_utils.py, excel_exporter.py)

NOTE: 1차 증분은 기록 중심이다. 자동 재고 차감은 기존 계량(weighing)과의 이중 차감을
방지하기 위해 후속 단계에서 통합한다.
"""

import json
import sqlite3
from datetime import datetime, timezone
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
    # 저울 해상도(2자리)에 맞춰 반올림 — 3자리 이론 계량량은 저울로 맞출 수 없다.
    return [round((w or 0) / base_total * total_amount, 2) for w in weights]


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
            # 파생 여부 — use_reactor 와 독립. 반응기 이월(carry-over) 허용 여부를 결정한다.
            "is_derived": recipe_is_derived(connection, int(recipe["id"])),
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


def mistake_stats(
    connection: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """작업자·자재별 이상(異常) 통계.

    배합은 계량 편차를 허용치(레시피별, 기본 0.05g) 이내로 강제(초과 시 저장 차단)하므로
    편차 자체는 이상 신호가 되지 못한다. 대신 실질적 이상 신호 두 가지를 집계한다:
      - 수동 입력(manual_entry): 저울 PRINT 가 아닌 손입력으로 계량된 것(저울 미사용).
      - 취소(status='canceled'): 잘못 등록해 취소된 기록.
    작업자별은 기록 단위(manual_entry 는 배치 플래그), 자재별은 상세 행 단위로 센다.
    """
    def _date_clause(col: str) -> tuple[str, list[Any]]:
        parts: list[str] = []
        vals: list[Any] = []
        if start_date:
            parts.append(f"{col} >= ?")
            vals.append(start_date)
        if end_date:
            parts.append(f"{col} <= ?")
            vals.append(end_date)
        return ((" AND " + " AND ".join(parts)) if parts else ""), vals

    wclause, wparams = _date_clause("work_date")
    worker_rows = connection.execute(
        f"""
        SELECT worker,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS records,
               SUM(CASE WHEN status = 'completed' AND manual_entry = 1 THEN 1 ELSE 0 END) AS manual_records,
               SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END) AS canceled_records
        FROM blend_records
        WHERE 1 = 1 {wclause}
        GROUP BY worker
        HAVING records > 0 OR canceled_records > 0
        ORDER BY manual_records DESC, canceled_records DESC, worker ASC
        """,
        wparams,
    ).fetchall()
    by_worker = []
    for r in worker_rows:
        records = int(r["records"] or 0)
        manual = int(r["manual_records"] or 0)
        by_worker.append({
            "worker": r["worker"],
            "records": records,
            "manual_records": manual,
            "canceled_records": int(r["canceled_records"] or 0),
            "manual_rate": round(manual / records * 100, 1) if records else 0.0,
        })

    mclause, mparams = _date_clause("r.work_date")
    material_rows = connection.execute(
        f"""
        SELECT d.material_name AS material_name,
               COUNT(*) AS rows_count,
               SUM(CASE WHEN d.manual_entry = 1 THEN 1 ELSE 0 END) AS manual_rows
        FROM blend_details d
        JOIN blend_records r ON r.id = d.blend_record_id
        WHERE r.status = 'completed' {mclause}
        GROUP BY d.material_name
        ORDER BY manual_rows DESC, d.material_name ASC
        """,
        mparams,
    ).fetchall()
    by_material = []
    for r in material_rows:
        rows_count = int(r["rows_count"] or 0)
        manual = int(r["manual_rows"] or 0)
        if manual == 0:
            continue  # 수동 입력이 한 번도 없는 자재는 이상 통계에 노출하지 않는다.
        by_material.append({
            "material_name": r["material_name"],
            "rows": rows_count,
            "manual_rows": manual,
            "manual_rate": round(manual / rows_count * 100, 1) if rows_count else 0.0,
        })
    return {"by_worker": by_worker, "by_material": by_material}


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


class CarryOverError(Exception):
    """반응기 이월(carry-over) 행의 검증 조건이 하나라도 어긋났다 — 400 으로 되돌린다."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def enforce_carry_over(
    connection: sqlite3.Connection,
    recipe_id: int | None,
    product_name: str,
    details: list[dict[str, Any]],
) -> None:
    """반응기 이월(carry-over) 행 검증 + 강제 채움. details 를 제자리(in-place) 수정.

    각 상세 행 중 carried_over=true 인 행은 아래 조건을 **모두** 만족해야 한다:
      1) 레시피가 파생(is_derived) 레시피일 것(recipe_is_derived 로 판정). use_reactor 와는
         독립 — 반응기 여부와 무관하게 파생 레시피에서만 이월이 허용된다.
      2) 그 행이 레시피의 기준 자재(anchor) 행일 것.
      3) 그 행의 material_lot 가 완료된 1차 배합 기록(product_name=이 자재명,
         product_lot=그 LOT, status='completed')에 존재할 것.
    통과하면 actual_amount 를 그 1차 기록의 total_amount 로 **강제** 덮어쓰고(클라이언트
    값 무시 — 변조 방지), manual_entry 는 false 로 강제한다. 어긋난 행이 있으면
    CarryOverError(400) 로 되돌린다(메시지에 자재명 포함).
    """
    reactor_rows = [d for d in details if d.get("carried_over")]
    if not reactor_rows:
        return  # 이월 행이 없으면 검사 자체를 건너뛴다(기존 동작 100% 유지).

    # 레시피가 파생인지 — use_reactor 와 무관하게 이것이 이월 허용 조건이다.
    recipe_is_derived_flag = recipe_is_derived(connection, recipe_id)
    # 레시피 기준 자재(material_name)를 미리 뽑아둔다 — 없는 구버전/테스트 DB 도 폴백.
    anchor_name: str | None = None
    if recipe_id:
        try:
            r = connection.execute(
                "SELECT anchor_material_id FROM recipes WHERE id = ?", (recipe_id,)
            ).fetchone()
            if r is not None and r["anchor_material_id"] is not None:
                m = connection.execute(
                    "SELECT name FROM materials WHERE id = ?", (int(r["anchor_material_id"]),)
                ).fetchone()
                if m is not None:
                    anchor_name = str(m["name"])
        except sqlite3.OperationalError:
            anchor_name = None

    for d in reactor_rows:
        mat_name = str(d.get("material_name") or "").strip()
        lot = (str(d.get("material_lot") or "").strip())
        if not recipe_is_derived_flag:
            raise CarryOverError(
                f"파생 이월({mat_name})은 파생 레시피에서만 사용할 수 있습니다."
            )
        if anchor_name is None or mat_name != anchor_name:
            raise CarryOverError(
                f"반응기 이월은 기준 자재({anchor_name or '없음'}) 행에만 지정할 수 있습니다: {mat_name}"
            )
        if not lot:
            raise CarryOverError(
                f"반응기 이월({mat_name}) 행에 1차 배합 LOT 가 비어 있습니다."
            )
        # 1차 완료 배합 기록 조회 — product_name=자재명, product_lot=LOT, status=completed.
        row = connection.execute(
            "SELECT total_amount FROM blend_records "
            "WHERE product_name = ? AND product_lot = ? AND status = 'completed' LIMIT 1",
            (mat_name, lot),
        ).fetchone()
        if row is None:
            raise CarryOverError(
                f"반응기 이월({mat_name}): 등록된 완료 LOT 가 아닙니다 — '{lot}'."
            )
        # 통과 — 실제량을 1차 배합 총량으로 강제 덮어쓰기(변조 방지), 수동입력 해제.
        d["actual_amount"] = float(row["total_amount"] or 0)
        d["manual_entry"] = False


def missing_lot_names(details: list[dict[str, Any]]) -> list[str]:
    """material_lot 가 비어 있는 행의 자재명 목록을 반환(LOT 입력 누락 검증).

    배합 실적은 자재별 LOT 가 추적성의 핵심이다 — LOT 없이 저장되면 어떤 원료 로트가
    쓰였는지 알 수 없어 불량 회수·이력 추적이 불가능하다. 따라서 enforce_carry_over 와
    derive_details_from_recipe 가 끝난 뒤(서버가 행을 보강한 최종 상태 기준) 모든 행의
    material_lot 가 strip() 후 비어있지 않아야 한다.

    carried_over(반응기 이월) 행은 enforce_carry_over 가 1차 배합 product_lot 를
    material_lot 로 요구·검증하므로 이 함수에 도달할 때 이미 채워져 있다 — 즉 본 검사에서
    자연스럽게 만족된다(특별 분기 불필요).

    반환: 빈 값인 행의 material_name 리스트(순서 보존). 호출부는 비어있지 않으면
    HTTPException(400, "자재 LOT 를 입력하세요: " + ...) 로 되돌린다.
    """
    missing: list[str] = []
    for d in details:
        lot = str(d.get("material_lot") or "").strip()
        if lot == "":
            missing.append(str(d.get("material_name") or "").strip() or "(이름 없음)")
    return missing


def unregistered_product_lots(
    connection: sqlite3.Connection,
    details: list[dict[str, Any]],
    overrides: list[dict[str, Any]] | None,
) -> list[str]:
    """미등록 반제품(자가 제품) LOT 가 있는지 서버 백업 검증 → 위반 "name/LOT" 목록.

    배합 화면은 자가 반제품(=완료 배합 기록이 있는 product_name)을 원료로 쓸 때 그
    material_lot 가 실제 완료 기록의 product_lot 인지 GET /blend/product-lot-exists 로
    확인한다. 미등록이면 '사유 적고 진행' 모달로 통과시킬 수 있으나, 클라이언트 검증은
    네트워크 장애 시 fail-open 으로 우회될 수 있고 서버는 재확인하지 않았다 — 오타 LOT 가
    그대로 저장되는 구멍. 이 함수가 저장 직전 서버에서 같은 규칙으로 재검증해 그 구멍을
    막는다(2026-07-22 사용자 결정: 사유 전달 시 통과, 아니면 차단).

    규칙(GET /blend/product-lot-exists 와 동일):
      - material_lot 가 비어있지 않은 행 중, material_name 이 completed 배합 기록의
        product_name 으로 존재하면(=자가 반제품) 그 LOT 도 completed 기록의 product_lot
        로 존재해야 한다.
      - carried_over 행은 enforce_carry_over 가 이미 1차 LOT 일치를 검증했으므로 제외.
      - overrides 에 (material_name, material_lot) 가 사유(reason 비어있지 않음) 와 함께
        있으면 그 행은 통과(운영자가 사유를 남긴 정당한 진행).
    반환: 위반 "name/LOT" 문자열 목록. 호출부는 비어있지 않으면 400 으로 되돌린다.
    """
    # 사유 승인 집합 — (name, lot) → reason. reason 이 빈 값이면 승인 아님.
    override_keys: set[tuple[str, str]] = set()
    for ov in overrides or []:
        try:
            ov_name = str(ov.get("material_name") or "").strip()
            ov_lot = str(ov.get("material_lot") or "").strip()
            ov_reason = str(ov.get("reason") or "").strip()
        except AttributeError:
            # Pydantic 모델 인스턴스인 경우 속성 접근.
            ov_name = str(getattr(ov, "material_name", "") or "").strip()
            ov_lot = str(getattr(ov, "material_lot", "") or "").strip()
            ov_reason = str(getattr(ov, "reason", "") or "").strip()
        if ov_name and ov_lot and ov_reason:
            override_keys.add((ov_name, ov_lot))

    offending: list[str] = []
    for d in details:
        if d.get("carried_over"):
            continue  # enforce_carry_over 가 이미 검증.
        name = str(d.get("material_name") or "").strip()
        lot = str(d.get("material_lot") or "").strip()
        if not name or not lot:
            continue
        # 자가 반제품 여부 — completed 배합 기록에 이 product_name 이 있는가.
        is_own = connection.execute(
            "SELECT 1 FROM blend_records "
            "WHERE product_name = ? AND status = 'completed' LIMIT 1",
            (name,),
        ).fetchone()
        if not is_own:
            continue  # 일반 원료 — LOT 등록 검증 대상 아님.
        if (name, lot) in override_keys:
            continue  # 사유 승인됨.
        # 자가 반제품이면 이 LOT 가 completed 기록의 product_lot 인지 확인.
        registered = connection.execute(
            "SELECT 1 FROM blend_records "
            "WHERE product_name = ? AND product_lot = ? AND status = 'completed' LIMIT 1",
            (name, lot),
        ).fetchone()
        if not registered:
            offending.append(f"{name}/{lot}")
    return offending


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
        # 저울 해상도(2자리) — 기준 자재 실측에서 파생하는 배치 총량도 2자리로 맞춘다
        # (자재 이론량은 2자리인데 총량만 3자리로 남는 불일치 방지).
        total = round(anchor_actual * 100.0 / ratio, 2)

    derived: list[dict[str, Any]] = []
    for order, item in enumerate(recipe["items"], start=1):
        name = str(item["material_name"])
        sent = incoming[name]
        ratio = float(item["ratio"] or 0)
        if anchor is not None:
            # 저울 해상도(2자리) — 기준 자재 파생 이론량도 2자리로 맞춘다.
            theory = anchor_actual if item.get("is_anchor") else round(total * ratio / 100.0, 2)
        else:
            theory = float(item["theory_amount"] or 0)
        derived.append({
            "material_id": item.get("material_id"),
            "material_code": item.get("material_code"),
            "material_name": name,
            "material_lot": sent.get("material_lot"),        # 사람만 아는 값
            "actual_amount": _opt_num(sent.get("actual_amount")),
            "manual_entry": bool(sent.get("manual_entry")),
            "carried_over": bool(sent.get("carried_over")),  # 반응기 이월 표식(사람이 지정)
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
    """제품명(레시피명)이 반응기 진행(use_reactor) 제품인지.

    반응기는 배합 실적을 진행한 위치이다. 반응기 사용 여부의 소유는 이제 레시피
    (recipes.use_reactor)로 이전되었다 — 같은 제품명의 가장 최근 completed 레시피
    (ORDER BY id DESC LIMIT 1) 값을 따른다. 매칭되는 레시피가 없으면(점도 전용
    레거시 제품 등) 구 점도 설정(viscosity_products.use_reactor)으로 폴백하여
    기존 동작을 유지한다. 실적 저장 시 이 값으로 반응기 지정을 강제할지 판단한다.
    """
    name = str(product_name or "").strip()
    if not name:
        return False
    # recipes.use_reactor 컬럼이 없는 레거시/단위테스트 스키마에서는 점도 폴백으로 간주.
    try:
        recipe_row = connection.execute(
            "SELECT use_reactor FROM recipes "
            "WHERE product_name = ? AND status = 'completed' "
            "ORDER BY id DESC LIMIT 1",
            (name,),
        ).fetchone()
    except sqlite3.OperationalError:
        recipe_row = None
    if recipe_row:
        return bool(recipe_row["use_reactor"])
    # 매칭되는 레시피가 없는 점도 전용 레거시 제품 — 구 값으로 폴밭.
    row = connection.execute(
        "SELECT use_reactor FROM viscosity_products "
        "WHERE code = ? OR name = ? ORDER BY use_reactor DESC LIMIT 1",
        (name, name),
    ).fetchone()
    return bool(row["use_reactor"]) if row else False


def recipe_is_derived(connection: sqlite3.Connection, recipe_id: int | None) -> bool:
    """레시피가 파생(is_derived) 레시피인지 — 앞 단계의 총량을 이월받아 다시 계량하지 않는지.

    use_reactor(반응기 번호 요구)와는 **독립**이다. 파생 여부가 반응기 이월(carry-over)
    허용 여부를 결정한다. recipe_id 가 None 이거나 컬럼이 없는 구버전/테스트 스키마에서는
    False(폴백) — anchor_material_id 조회와 동일한 try/except 방어.
    """
    if recipe_id is None:
        return False
    try:
        row = connection.execute(
            "SELECT is_derived FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
    except sqlite3.OperationalError:  # is_derived 컬럼이 없는 구버전/테스트 DB
        return False
    return bool(row["is_derived"]) if row else False


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
                 manual_entry, carried_over, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if d.get("carried_over") else 0,
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
                 manual_entry, carried_over, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if d.get("carried_over") else 0,
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
               ratio, theory_amount, actual_amount, sequence_order, manual_entry,
               carried_over
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
        "carried_over": bool(row["carried_over"]) if "carried_over" in row.keys() else False,
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


# ── 증량(rescale) 승인 — 책임자 인증 토큰 발급·소비 ─────────────
_RESCALE_APPROVAL_TTL_MINUTES = 30  # 승인 유효 시간(분) — 30분 내 저장에만 쓸 수 있다.


def create_rescale_approval(connection: sqlite3.Connection, approver: str) -> dict[str, Any]:
    """책임자 인증 성공 시 blend_rescale_approvals 행을 INSERT 하고 {id, approver} 반환.

    증량(rescale) 은 2회까지 책임자 현장 인증으로 허용되며, 그 인증 토큰을 발급한다.
    실제 소비(used=1 표시)는 저장 시 validate_rescale_events 에서 이루어진다.
    """
    from ..db.time_utils import utc_now_text

    cursor = connection.execute(
        "INSERT INTO blend_rescale_approvals (approver, created_at, used) VALUES (?, ?, 0)",
        (approver, utc_now_text()),
    )
    return {"approval_id": cursor.lastrowid, "approver": approver}


class RescaleApprovalError(Exception):
    """증량 승인 검증 실패 — detail 메시지를 그대로 400 으로 반환한다."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _iso_to_dt(text: str) -> datetime | None:
    """ISO 형식 문자열 → datetime(파싱 실패/빈 값 → None). 승인 만료 판정용."""
    if not text:
        return None
    try:
        # utc_now_text 가 'YYYY-MM-DDTHH:MM:SS...Z' 형태 — Z 를 +00:00 로 정규화.
        norm = text.replace("Z", "+00:00")
        return datetime.fromisoformat(norm)
    except (ValueError, TypeError):
        return None


def validate_rescale_events(
    connection: sqlite3.Connection,
    events: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """저장 본문의 rescale_events 를 검증·정규화 → 저장용 딕셔너리 반환(또는 None).

    각 event 는 {before_total, after_total, approval_id?, absence_reason?, worker_confirmed?}:
      - approval_id 가 있으면: 미사용(used=0)·30분 이내 행이어야 한다. 통과 시 used=1
        표시하고 approver(책임자 표시명) 를 event 에 채운다.
      - approval_id 가 없으면 absence_reason(비어있지 않은 사유) 이 필수 — 그 event 는
        미승인(absence) 으로 기록되어 rescale_unacked=1 을 유발한다.
      - 둘 다 없거나 approval_id 가 유효하지 않으면 RescaleApprovalError(400).

    반환: {events_json, count, unacked} — events_json 은 정규화된 event 목록의 JSON 문자열,
    count 는 event 수, unacked 는 미승인(absence) event 가 하나라도 있으면 1 아니면 0.
    events 가 None/빈 리스트면 None 반환(기존 저장 동작 100% 유지 — 컬럼 기본값 유지).
    """
    if not events:
        return None
    if len(events) > 2:
        raise RescaleApprovalError(
            "3회 증량은 불가합니다 — 책임자와 폐기 여부를 협의하세요."
        )

    from ..db.time_utils import utc_now_text

    now = utc_now_text()
    now_dt = _iso_to_dt(now) or datetime.now(timezone.utc)
    normalized: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    has_absence = False
    unapproved = 0

    for ev in events:
        approval_id = ev.get("approval_id")
        absence_reason = str(ev.get("absence_reason") or "").strip()
        norm_ev: dict[str, Any] = {
            "before_total": ev.get("before_total"),
            "after_total": ev.get("after_total"),
            "worker_confirmed": bool(ev.get("worker_confirmed")),
        }
        if approval_id is not None:
            # 승인 행 조회 — used=0 이고 30분 이내여야 한다.
            row = connection.execute(
                "SELECT id, approver, created_at, used FROM blend_rescale_approvals WHERE id = ?",
                (int(approval_id),),
            ).fetchone()
            if not row or row["used"]:
                raise RescaleApprovalError(
                    "증량 승인이 유효하지 않습니다 — 다시 인증하세요."
                )
            created_dt = _iso_to_dt(row["created_at"])
            if created_dt is None or (now_dt - created_dt).total_seconds() > _RESCALE_APPROVAL_TTL_MINUTES * 60:
                raise RescaleApprovalError(
                    "증량 승인이 유효하지 않습니다 — 다시 인증하세요."
                )
            # 소비 표시 — 같은 approval_id 재사용 방지.
            connection.execute(
                "UPDATE blend_rescale_approvals SET used = 1 WHERE id = ?",
                (int(approval_id),),
            )
            norm_ev["approval_id"] = int(row["id"])
            norm_ev["approver"] = row["approver"]
        elif absence_reason:
            # 미승인(absence) — 책임자 없이 진행한 경우, 사유 필수.
            norm_ev["absence_reason"] = absence_reason
            has_absence = True
            unapproved += 1
        else:
            raise RescaleApprovalError(
                "증량 승인이 유효하지 않습니다 — 다시 인증하세요."
            )
        normalized.append(norm_ev)
        totals.append(
            {"before_total": norm_ev["before_total"], "after_total": norm_ev["after_total"]}
        )

    return {
        "events_json": json.dumps(normalized, ensure_ascii=False),
        "events": normalized,
        "count": len(normalized),
        "unacked": 1 if has_absence else 0,
        "unapproved": unapproved,
        "totals": totals,
    }


def apply_rescale_to_record(
    connection: sqlite3.Connection,
    record_id: int,
    validated: dict[str, Any],
) -> None:
    """검증된 증량(rescale) 정보를 blend_records 행에 기록한다.

    validate_rescale_events 가 돌려준 딕셔너리(events_json/count/unacked)를 받아
    rescale_events_json·rescale_count·rescale_unacked 컬럼을 갱신한다. 이벤트가 없어
    validated=None 인 경우 호출부에서 건너뛰므로(컬럼 기본값 0 유지) 여기선 항상 값이 있다.
    """
    connection.execute(
        "UPDATE blend_records SET rescale_events_json = ?, rescale_count = ?, "
        "rescale_unacked = ? WHERE id = ?",
        (validated["events_json"], validated["count"], validated["unacked"], record_id),
    )
