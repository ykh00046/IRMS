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
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db.queries import normalize_token
from .recipe_helpers import SUPERSEDED_RECIPE_IDS_SQL, resolve_chain_tip

logger = logging.getLogger(__name__)

# 대시보드/분석 집계 상한 — 무제한 반환으로 응답이 폭증하는 것을 막는다(no silent truncation:
# 상한을 넘으면 truncated 플래그 + 경고 로그를 남기고, 조용히 잘라내지 않는다).
_MATERIAL_USAGE_MAX_ITEMS = 5000
_BATCH_DETAILS_MAX_ROWS = 10000


# ── 총 배합량 상한·이상 감지 (2026-08-04) ─────────────────────────────────────
# 서버가 받아들이는 배합 총량의 절대 상한(g). 예전 값 10,000,000(10톤)은 사실상
# 제약이 아니었다 — 총량 칸에 자릿수를 잘못 친 값이 그대로 DHR·자재 사용량 집계에
# 실려도 아무것도 막지 않았다.
#
# 200,000 g(200 kg)으로 정한 근거:
#   ① 현장 1회 배합 허용 상한은 25,000 g 이다(화면 blend_lib.BATCH_LIMIT_G, 초과 시
#      폐기 권장 모달). 정상 배치는 여기서 끝난다.
#   ② 그 위로 갈 수 있는 정당한 경로는 초과 계량 증량뿐이고, 증량은 기록당 최대 2회다
#      (3회째는 화면·서버 모두 차단). 한 번의 증량이 현실적으로 총량을 두 배 넘게
#      밀어 올리는 경우는 자재를 통째로 두 번 부은 사고 정도이므로, 최악의 현실
#      시나리오는 25,000 × 2 × 2 = 100,000 g 이다.
#   ③ 그 두 배(=현장 상한의 8배)를 상한으로 둔다. 정당한 '그래도 증량'이 이 선에
#      닿는 일은 없고, 반대로 10 kg 이상 배치에서 0 하나를 더 친 오타는 전부 막힌다.
BLEND_TOTAL_MAX_G = 200_000.0

# 현장 1회 배합 허용 상한(화면 blend_lib.BATCH_LIMIT_G 와 같은 값).
# **저장을 막지 않는다** — 폐기 권장을 무시하는 '그래도 증량'은 계속 살아 있어야 한다.
# 넘긴 저장은 blend_records.oversize_total = 1 로만 남고 책임자 대사 화면에 뜬다.
BLEND_OVERSIZE_FLAG_G = 25_000.0

# ── 증량 승인 우회 감지 임계값 ────────────────────────────────────────────────
# 우회 시나리오: 초과 계량 후 승인 모달을 받는 대신 새로고침하고 총량을 먼저 키워
# 입력하면, 전 자재가 편차 이내가 되어 rescale_count=0 인 '정상 배치'로 저장된다.
# 서버에서 남는 유일한 흔적은 "증량 이력이 없는데 총량이 레시피 기준과 다르다" 뿐이다.
#
# 감지는 기록만 남기고 저장은 절대 막지 않는다(정당한 커스텀 총량 차단 = 현장 정지).
# 그래서 임계는 전부 '의심스러울 때만 켜지는' 방향으로 보수적으로 잡았다.
BYPASS_EXCESS_RATIO = 0.05      # 기준 배합량 대비 5% 초과 상향일 때만
BYPASS_EXCESS_MIN_G = 50.0      # 동시에 절대 초과분 50 g 초과 (작은 레시피 보호)
BYPASS_MULTIPLE_TOL = 0.005     # 기준의 정수배(2배·3배 배치)는 ±0.5% 이내면 정상 관행
BYPASS_ROUND_STEP_G = 10.0      # 10 g 단위로 떨어지는 총량은 '손으로 친 값'으로 보고 제외


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
    connection: sqlite3.Connection,
    recipe_id: int,
    total_amount: float | None = None,
    *,
    resolve_revision: bool = True,
) -> dict[str, Any] | None:
    """레시피와 자재 목록을 비율·이론량과 함께 반환 (배합 입력 화면용).

    total_amount 미지정 시 레시피 절대중량 합계를 기본 배치 총량으로 사용.
    개정된 레시피 id 가 오면 최신 개정판으로 자동 귀결.

    resolve_revision=False: **이미 저장된 기록을 수정할 때** 쓴다. 그 기록이 만들어진
    개정본을 그대로 써야 한다 — 최신 개정판으로 귀결시키면 작업시간·비고만 고쳐도
    배합비율·이론량이 새 레시피 값으로 조용히 바뀌어 규제 문서(DHR)의 과거 값이
    변조되고, 개정 이후에는 편차 초과 400 으로 메타 정정조차 막힌다.
    """
    if resolve_revision:
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
    """normalize_token(자재명) → materials.code(ERP 품목코드) 매핑. P4 최우선 ERP 코드 출처.

    구버전 DB(materials.code 컬럼 없음)는 빈 맵 폴백 — recipe_tolerance_g 의
    OperationalError 방어 패턴과 동일. NULL 코드는 제외(미부여=빈 값).

    키는 normalize_token(대문자화 + 공백/기호 제거)으로 잡는다 — 마스터 매칭 전반과
    같은 정규화. 기록의 material_name 이 자재명과 대소문자/내부 공백만 달라도(예:
    'HEMA (Lotte)' vs 'HEMA(Lotte)') 1순위 materials.code 매핑이 누락되지 않게 한다(GAP).
    """
    try:
        rows = connection.execute(
            "SELECT name, code FROM materials WHERE code IS NOT NULL AND code <> ''"
        ).fetchall()
    except sqlite3.OperationalError:  # code 컬럼이 없는 구버전/테스트 DB
        return {}
    return {
        normalize_token(r["name"] or ""): (r["code"] or "").strip()
        for r in rows
        if r["code"] and (r["name"] or "").strip()
    }


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


# ERP 품목코드 형태(영문 1-3자 + 숫자 3자 이상, 예: AC0060/AS0031/B0020).
# materials.code 미등록 자재의 저장 코드 fallback 판별용.
_ERP_CODE_RE = re.compile(r"^[A-Za-z]{1,3}\d{3,}$")


def _resolve_erp_code(
    name: str,
    code: str,
    alias_map: dict[str, str],
    material_code_map: dict[str, str] | None = None,
) -> str:
    """ERP 품목코드 결정. 우선순위(P4):
    materials.code > RM 별칭 > RM 형태 저장 코드 > RM 형태 자재명
    > ERP 형태 저장 코드 > 별칭(비RM).

    materials.code 가 도입되기 전 화면 '자재코드'는 materials.category(분류) 였고,
    이 인자 `code` 는 그 legacy 값을 받는다 — RM/ERP 형태인 경우에만 후보로 쓴다.
    """
    # 1) materials.code — 정식 ERP 품목코드(P4 최우선). 맵과 같은 정규화 키로 조회.
    if material_code_map is not None:
        mc = material_code_map.get(normalize_token(name or ""), "")
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
    # 5) ERP 품목코드 형태의 저장 코드 — materials.code 미등록이어도 상세 화면에서
    #    정식 코드(AC0060 등)를 직접 입력한 자재는 그 코드로 매칭한다.
    if _ERP_CODE_RE.fullmatch((code or "").strip()):
        return (code or "").strip()
    # 6) 비RM 별칭이라도 있으면 제공(빈 행 skip 회피)
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
        WHERE br.status = 'completed' AND COALESCE(br.is_bulk_regenerated, 0) = 0
          AND br.work_date >= ? AND br.work_date <= ?
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
        "WHERE br.status = 'completed' AND COALESCE(br.is_bulk_regenerated, 0) = 0 "
        "AND br.work_date >= ? AND br.work_date <= ?",
        (start_date, end_date),
    ).fetchone()[0]
    # by_product=True + group=day 로 넓은 기간을 요청하면 items 가 자재×제품×일수로 폭증할 수
    # 있어 상한을 둔다. 넘으면 잘라내되 truncated 로 표면화(조용한 절단 금지).
    # total_weight 는 절단 전 전체 합으로 계산(부분합으로 총량이 과소 보고되는 것 방지).
    total_items = len(items)
    total_weight = round(sum(i["total_actual"] for i in items), 3)
    truncated = total_items > _MATERIAL_USAGE_MAX_ITEMS
    if truncated:
        logger.warning(
            "material_usage_periods truncated: %d items > cap %d "
            "(start=%s end=%s group=%s by_product=%s)",
            total_items, _MATERIAL_USAGE_MAX_ITEMS, start_date, end_date, group, by_product,
        )
        items = items[:_MATERIAL_USAGE_MAX_ITEMS]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "group": group,
        "unit": "g",
        "record_count": int(rec_count),
        "total_weight": total_weight,
        "items": items,
        "truncated": truncated,
        "total_item_count": total_items,
    }


def material_usage(
    connection: sqlite3.Connection,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """배합 기록 기반 자재 사용 분석. 기간 내 완료 기록의 자재별 실제/이론 사용량·건수."""
    where = ["br.status = 'completed'", "COALESCE(br.is_bulk_regenerated, 0) = 0"]
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
    where = ["status = 'completed'", "COALESCE(is_bulk_regenerated, 0) = 0"]
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
        WHERE COALESCE(is_bulk_regenerated, 0) = 0 {wclause}
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
        WHERE r.status = 'completed' AND COALESCE(r.is_bulk_regenerated, 0) = 0 {mclause}
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
    where = ["br.status = 'completed'", "COALESCE(br.is_bulk_regenerated, 0) = 0"]
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
    effective_limit = max(1, min(int(limit), _BATCH_DETAILS_MAX_ROWS))
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
        [*params, effective_limit],
    ).fetchall()
    # LIMIT 에 정확히 걸리면 더 있을 수 있으므로 truncated 로 표면화(조용한 절단 금지).
    truncated = len(rows) >= effective_limit
    if truncated:
        logger.warning(
            "batch_details truncated at LIMIT %d (start=%s end=%s product=%s) — "
            "결과가 상한에 도달해 일부가 잘렸을 수 있음",
            effective_limit, start_date, end_date, product,
        )
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
        "truncated": truncated,
        "limit": effective_limit,
    }


def material_usage_details(
    connection: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    limit: int = _BATCH_DETAILS_MAX_ROWS,
) -> dict[str, Any]:
    """자재 불출 행 단위 상세 — 상위 재고 대시보드 LOT 배정(FIFO 정리) 연동용.

    batch_details(완료 기록 평면 목록)에 erp_code(재고 시스템 매칭 키)를 결합한다.
    해석 체계는 집계 API(material_usage_periods)와 동일(_resolve_erp_code) —
    두 API 가 같은 자재를 다른 코드로 보고하지 않도록 단일 소스를 공유한다.
    """
    result = batch_details(connection, start_date, end_date, None, limit=limit)
    alias_map = _erp_code_map(connection)
    material_code_map = _material_code_map(connection)
    for item in result["items"]:
        item["erp_code"] = _resolve_erp_code(
            item["material_name"], item["material_code"] or "", alias_map, material_code_map
        )
    result["unit"] = "g"
    return result


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
    # LIMIT 에 정확히 걸리면 더 있을 수 있으므로 truncated 로 표면화(batch_details 와 동일 패턴).
    truncated = len(items) >= int(limit)
    return {
        "lot": clean,
        "items": items,
        "total": len(items),
        "record_count": len({it["record_id"] for it in items}),
        "truncated": truncated,
        "limit": int(limit),
    }


def list_blend_recipes(connection: sqlite3.Connection, *, dhr: bool = False) -> list[dict[str, Any]]:
    """배합에 쓸 수 있는 레시피 목록 (취소/초안 제외).

    dhr=False(기본): 일반 레시피. dhr=True: DHR 전용 레시피(일괄 배합일지 생성용).
    """
    rows = connection.execute(
        """
        SELECT r.id, r.product_name, r.position, r.ink_name, r.status, r.category,
               r.product_code, r.stage1_recipe_id,
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
    items = [
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
            "stage1_recipe_id": (
                int(r["stage1_recipe_id"]) if r["stage1_recipe_id"] is not None else None
            ),
            "item_count": int(r["item_count"]),
            "total_weight": round(float(r["total_weight"]), 3),
        }
        for r in rows
    ]
    return _cluster_recipe_families(items)


def _cluster_recipe_families(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """레시피 목록을 2단 제조 가족(1차↔2차)끼리 인접하게 재정렬한다.

    배합·이어서 계량 드롭다운에서 가족이 흩어져 보이던 문제 해결. 전체 순서는 기존
    최신순을 유지하되, 각 가족은 '가족의 첫 등장 위치'에 함께 모아 최종(2차)을 먼저,
    이어서 1차(중간체)를 붙인다. 관계는 명시 링크(stage1_recipe_id) 우선, 없으면 이름
    규칙("<base>" 과 "<base>-1")으로 보완(현황 가족 묶음과 동일 기준).
    """
    by_id = {it["id"]: it for it in items}
    id_by_name: dict[str, int] = {}
    for it in items:
        id_by_name.setdefault(str(it["product_name"] or ""), it["id"])

    # leader[id] = 그 가족의 대표(최종/2차) 레시피 id. 기본은 자기 자신.
    leader = {it["id"]: it["id"] for it in items}
    # 1) 명시 링크: 2차 F 가 가리키는 1차 S 는 F 가족으로.
    for it in items:
        s1 = it.get("stage1_recipe_id")
        if s1 is not None and s1 in by_id:
            leader[s1] = it["id"]
    # 2) 이름 규칙 보완: "<base>-1" 은 "<base>" 가족으로(명시 링크 없을 때).
    for it in items:
        name = str(it["product_name"] or "")
        if name.endswith("-1"):
            base_id = id_by_name.get(name[:-2])
            if base_id is not None and base_id != it["id"] and leader[it["id"]] == it["id"]:
                leader[it["id"]] = base_id

    def resolve(i: int) -> int:
        seen: set[int] = set()
        while leader[i] != i and i not in seen:
            seen.add(i)
            i = leader[i]
        return i

    from collections import defaultdict

    members: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        members[resolve(it["id"])].append(it)

    emitted: set[int] = set()
    ordered: list[dict[str, Any]] = []
    for it in items:  # 최신순 유지 — 가족은 첫 등장 위치에 모은다.
        lid = resolve(it["id"])
        if lid in emitted:
            continue
        emitted.add(lid)
        grp = members[lid]
        # 대표(2차)를 먼저, 나머지(1차)는 이름순.
        grp.sort(key=lambda x: (0 if x["id"] == lid else 1, str(x["product_name"] or "")))
        ordered.extend(grp)
    return ordered


# ── product_lot 생성 ────────────────────────────────────────────
def generate_product_lot(
    connection: sqlite3.Connection, product_name: str, work_date: str
) -> str:
    """{제품명}{YYMMDD}{순번:02d}. 같은 날 같은 제품의 기존 최대 순번+1."""
    digits = "".join(ch for ch in work_date if ch.isdigit())
    yymmdd = digits[2:8] if len(digits) >= 8 else digits[-6:]
    base = f"{product_name.strip()}{yymmdd}"
    # 접두 검색을 LIKE 대신 범위 비교로 한다. SQLite 는 ESCAPE 절이 붙으면 LIKE 최적화를
    # 아예 포기해 인덱스가 있어도 전체 스캔이 된다 — 이 함수는 저장할 때마다, 그리고
    # /blend/next-lot 미리보기마다 호출되고 BEGIN IMMEDIATE 안에서 돌아 쓰기 락을 잡는다.
    # 범위 비교는 기존 product_lot 인덱스를 그대로 타서 기록이 10배로 늘어도 비용이 없다.
    # (\U0010FFFF = 유니코드 최대 코드포인트 → base 로 시작하는 모든 문자열의 상한)
    rows = connection.execute(
        "SELECT product_lot FROM blend_records "
        "WHERE product_lot >= ? AND product_lot < ?",
        (base, base + "\U0010FFFF"),
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


def _override_field(ov: Any, field: str) -> Any:
    """dict 또는 Pydantic 모델에서 필드 하나를 꺼낸다(둘 다 오는 호출부가 있다)."""
    if isinstance(ov, dict):
        return ov.get(field)
    return getattr(ov, field, None)


def unregistered_product_lot_pairs(
    connection: sqlite3.Connection,
    details: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """앞 단계 배합 기록에 없는 자가 반제품 LOT (name, lot) 쌍 목록(중복 제거·순서 보존).

    규칙(GET /blend/product-lot-exists 와 동일):
      - material_lot 가 비어있지 않은 행 중, material_name 이 completed 배합 기록의
        product_name 으로 존재하면(=자가 반제품) 그 LOT 도 completed 기록의 product_lot
        로 존재해야 한다.
      - carried_over 행은 enforce_carry_over 가 이미 1차 LOT 일치를 검증했으므로 제외.

    **이 함수는 판정만 한다 — 저장을 막지 않는다.** 2026-08-04 이전에는 결과가 비어있지
    않으면 400 이었는데, 1차 배합을 만들고 곧바로 2차에 투입하는 정당한 경우에도 매번
    걸려 작업자가 사유란에 아무 글자나 치고 넘어갔다(통제의 형해화). 지금은 화면이
    가벼운 확인 창만 띄우고, 서버는 이 판정 결과를 blend_lot_acks 에 기록으로 남긴다.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for d in details:
        if d.get("carried_over"):
            continue  # enforce_carry_over 가 이미 검증.
        name = str(d.get("material_name") or "").strip()
        lot = str(d.get("material_lot") or "").strip()
        if not name or not lot or (name, lot) in seen:
            continue
        # 자가 반제품 여부 — completed 배합 기록에 이 product_name 이 있는가.
        is_own = connection.execute(
            "SELECT 1 FROM blend_records "
            "WHERE product_name = ? AND status = 'completed' LIMIT 1",
            (name,),
        ).fetchone()
        if not is_own:
            continue  # 일반 원료 — LOT 등록 검증 대상 아님.
        # 자가 반제품이면 이 LOT 가 completed 기록의 product_lot 인지 확인.
        registered = connection.execute(
            "SELECT 1 FROM blend_records "
            "WHERE product_name = ? AND product_lot = ? AND status = 'completed' LIMIT 1",
            (name, lot),
        ).fetchone()
        if not registered:
            seen.add((name, lot))
            pairs.append((name, lot))
    return pairs


def collect_lot_acks(
    connection: sqlite3.Connection,
    details: list[dict[str, Any]],
    overrides: list[Any] | None,
) -> list[dict[str, Any]]:
    """저장에 포함된 '앞 단계 기록에 없는 반제품 LOT' 를 대사용 구조화 항목으로 만든다.

    반환 항목: {material_name, material_lot, reason, acknowledged}
      - reason        화면에서 작업자가 적은 사유(선택 — 빈 문자열 가능)
      - acknowledged  작업자가 확인 창의 '계속' 을 눌렀는가. 화면을 거치지 않은 경로
                      (조회 실패 fail-open, 붙여넣기 등)로 저장된 건은 False 로 남아
                      대사 화면이 "확인 절차를 거치지 않은 진행" 을 구분할 수 있다.

    **사유가 비어도 항목을 버리지 않는다** — 사유가 선택이 된 순간 사유를 필터 조건으로
    쓰면 대사 신호가 통째로 사라진다(구 buildLotOverrides 의 결함). 판정 기준은 오직
    "지금 이 LOT 이 앞 단계 completed 기록에 없는가" 다.

    클라이언트가 보낸 overrides 중 저장 시점에 이미 등록된 LOT(그 사이 1차가 저장됨)은
    자연히 빠진다 — 대사가 자기 치유된다.
    """
    supplied: dict[tuple[str, str], dict[str, Any]] = {}
    for ov in overrides or []:
        ov_name = str(_override_field(ov, "material_name") or "").strip()
        ov_lot = str(_override_field(ov, "material_lot") or "").strip()
        if not ov_name or not ov_lot:
            continue
        ack = _override_field(ov, "acknowledged")
        supplied[(ov_name, ov_lot)] = {
            "reason": str(_override_field(ov, "reason") or "").strip()[:500],
            # acknowledged 미전송(구 클라이언트)은 True 로 본다 — 옛 화면은 사유 입력
            # 모달을 통과해야만 override 를 보냈으므로 확인을 거친 것이 맞다.
            "acknowledged": True if ack is None else bool(ack),
        }

    acks: list[dict[str, Any]] = []
    for name, lot in unregistered_product_lot_pairs(connection, details):
        info = supplied.get((name, lot))
        acks.append({
            "material_name": name,
            "material_lot": lot,
            "reason": (info or {}).get("reason", ""),
            "acknowledged": bool((info or {}).get("acknowledged", False)),
        })
    return acks


def record_lot_acks(
    connection: sqlite3.Connection,
    record_id: int,
    acks: list[dict[str, Any]],
    created_at: str,
    *,
    replace: bool = False,
) -> int:
    """blend_lot_acks 에 대사용 행을 남긴다 → 남긴 행 수.

    replace=True 는 수정(PUT) 경로용 — 그 기록의 옛 행을 지우고 현재 상태로 다시 쓴다
    (수정으로 LOT 가 바뀌면 옛 대사 대상이 유령으로 남는다).
    """
    if replace:
        connection.execute("DELETE FROM blend_lot_acks WHERE record_id = ?", (record_id,))
    for ack in acks:
        connection.execute(
            "INSERT INTO blend_lot_acks "
            "(record_id, material_name, material_lot, reason, acknowledged, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                record_id,
                ack["material_name"],
                ack["material_lot"],
                ack.get("reason") or "",
                1 if ack.get("acknowledged") else 0,
                created_at,
            ),
        )
    return len(acks)


# ── 총 배합량 플래그(B) · 증량 승인 우회 감지(C) ──────────────────────────────


def is_oversize_total(total_amount: Any) -> bool:
    """현장 1회 배합 상한(25,000 g)을 넘긴 총량인가 — 저장 차단이 아니라 표시용 판정."""
    try:
        return float(total_amount) > BLEND_OVERSIZE_FLAG_G
    except (TypeError, ValueError):
        return False


def recipe_base_totals(connection: sqlite3.Connection, recipe_id: int | None) -> list[float]:
    """레시피에 지정된 기준 배합량(g) 목록. 미지정·구버전 DB·없는 id 는 빈 목록.

    get_recipe_for_blend 의 default_totals 와 같은 규칙(base_totals CSV 우선,
    없으면 구 단일 base_total 폴백, 0 이하 제외, 최대 3개)이지만 레시피 전체를
    환산하지 않고 컬럼만 읽는다 — 저장 경로에서 매번 부르는 값이라 가볍게 둔다.
    """
    if not recipe_id:
        return []
    try:
        row = connection.execute(
            "SELECT base_total, base_totals FROM recipes WHERE id = ?", (int(recipe_id),)
        ).fetchone()
    except sqlite3.OperationalError:  # 구버전/테스트 DB — 컬럼 부재
        return []
    if row is None:
        return []
    totals: list[float] = []
    raw = row["base_totals"]
    if raw:
        for token in str(raw).split(","):
            token = token.strip()
            try:
                value = float(token)
            except ValueError:
                continue
            if value > 0 and value not in totals:
                totals.append(value)
    elif row["base_total"] is not None:
        try:
            value = float(row["base_total"])
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            totals.append(value)
    return totals[:3]


def recipe_has_anchor(connection: sqlite3.Connection, recipe_id: int | None) -> bool:
    """기준 자재(anchor) 레시피인가 — 우회 감지에서 통째로 제외할지 판정.

    기준 자재 레시피는 총량이 **기준 자재 실측에서 파생**된다
    (derive_details_from_recipe). 즉 총량이 기준 배합량과 다른 것이 정상이고
    값도 라운드가 아니다 — 감지 대상으로 두면 전부 오탐이 된다.
    반응기 이월(파생) 레시피도 이월 행이 기준 자재라 여기에 함께 걸린다.
    """
    if not recipe_id:
        return False
    try:
        row = connection.execute(
            "SELECT anchor_material_id FROM recipes WHERE id = ?", (int(recipe_id),)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row is not None and row["anchor_material_id"] is not None)


def detect_total_bypass(
    connection: sqlite3.Connection,
    recipe_id: int | None,
    total_amount: Any,
    *,
    rescale_count: int = 0,
) -> float | None:
    """증량 승인 우회 의심 판정 → 비교 기준으로 삼은 기준 배합량(g), 아니면 None.

    **판정만 한다 — 저장을 막지 않는다.** 정당한 커스텀 총량이 차단되면 현장이 멈춘다.

    아래를 모두 만족할 때만 의심으로 본다(하나라도 어긋나면 None):
      1. 증량 이력이 없다(rescale_count = 0). 있으면 통제가 정상 작동한 배치다.
      2. 레시피 연계 기록이고, 그 레시피에 기준 배합량이 지정돼 있다.
         기준이 없는 레시피(현장에 많다)는 비교할 근거 자체가 없어 영영 제외된다.
      3. 기준 자재(anchor) 레시피가 아니다 — 총량이 실측 파생이라 비교가 성립 안 함.
      4. 총량이 **모든** 기준 배합량보다 크다. 하향(분할 배합)·기준 사이 값은 제외 —
         우회는 항상 총량을 키우는 방향이다.
      5. 초과분이 기준의 5% 초과 **그리고** 절대 50 g 초과.
      6. 어떤 기준의 정수배(2배·3배 …, ±0.5%)도 아니다 — 배수 배치는 정상 관행.
      7. 총량이 10 g 단위로 떨어지지 않는다.
         우회 총량은 '초과 계량한 실측 × 100 / 비율' 에 사실상 고정된다(허용 편차가
         0.05 g 수준이라 창이 아주 좁다) → 끝자리가 살아 있는 값이 된다. 반대로
         작업자가 의도적으로 정하는 커스텀 총량은 5,000 · 12,000 처럼 라운드다.
         이 조건이 오탐의 주 방어선이다(대신 라운드로 맞춰 친 우회는 놓친다 —
         사용자 결정이 '기록만 남긴다' 이므로 과탐보다 미탐을 택했다).
    """
    if rescale_count:
        return None
    try:
        total = float(total_amount)
    except (TypeError, ValueError):
        return None
    if not (total > 0):
        return None
    if recipe_has_anchor(connection, recipe_id):
        return None
    bases = recipe_base_totals(connection, recipe_id)
    if not bases:
        return None
    if any(total <= b for b in bases):
        return None                       # 상향 초과가 아니다(기준 이하이거나 기준 사이)
    base = max(bases)                     # 가장 가까운(=가장 큰) 기준을 비교 대상으로
    excess = total - base
    if excess <= BYPASS_EXCESS_MIN_G or excess <= base * BYPASS_EXCESS_RATIO:
        return None
    for b in bases:
        n = round(total / b)
        if n >= 2 and abs(total - b * n) <= b * n * BYPASS_MULTIPLE_TOL:
            return None                   # 기준의 정수배 배치 — 정상 관행
    nearest_round = round(total / BYPASS_ROUND_STEP_G) * BYPASS_ROUND_STEP_G
    if abs(total - nearest_round) < 1e-6:
        return None                       # 손으로 친 라운드 총량
    return base


def apply_total_flags(
    connection: sqlite3.Connection,
    record_id: int,
    total_amount: Any,
    *,
    recipe_id: int | None,
    rescale_count: int = 0,
) -> dict[str, Any]:
    """총량 플래그 2종을 기록에 반영 → {oversize_total, total_bypass_suspect, total_bypass_base}.

    저장 성공 후(record_id 확보 뒤) 호출한다. 어느 쪽도 저장을 막지 않는다.
    수정(PUT) 경로에서도 같은 함수를 부르면 총량이 정정될 때 플래그가 함께 갱신된다
    (0 으로 되돌아가는 것도 정상 — 잘못 친 총량을 고쳤다는 뜻).
    """
    oversize = is_oversize_total(total_amount)
    base = detect_total_bypass(
        connection, recipe_id, total_amount, rescale_count=rescale_count
    )
    connection.execute(
        "UPDATE blend_records SET oversize_total = ?, total_bypass_suspect = ?, "
        "total_bypass_base = ? WHERE id = ?",
        (1 if oversize else 0, 1 if base is not None else 0, base, record_id),
    )
    return {
        "oversize_total": oversize,
        "total_bypass_suspect": base is not None,
        "total_bypass_base": base,
    }


def derive_details_from_recipe(
    connection: sqlite3.Connection,
    recipe_id: int,
    total_amount: float,
    details: list[dict[str, Any]],
    *,
    resolve_revision: bool = True,
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
    recipe = get_recipe_for_blend(
        connection, recipe_id, total_amount, resolve_revision=resolve_revision
    )
    if not recipe:
        raise RecipeMismatchError("레시피를 찾을 수 없습니다.")

    # 같은 자재가 레시피에 여러 번 나올 수 있다(분할 계량 — 예: PB 의 Cyclopentanone
    # 2000g+3510g). 이름 키 딕셔너리로 짝지으면 중복 행이 조용히 소실되거나 엉뚱한
    # 이론량과 짝지어져 정상 계량이 편차 초과로 오판된다(현장 신고 2026-07-24).
    # → 이름별 '등장 순서 보존 그룹'으로 모아 k번째는 k번째와 짝짓는다.
    from collections import defaultdict

    incoming_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in details:
        incoming_groups[str(d.get("material_name") or "")].append(d)
    item_counts: dict[str, int] = defaultdict(int)
    for it in recipe["items"]:
        item_counts[str(it["material_name"])] += 1

    if dict(item_counts) != {k: len(v) for k, v in incoming_groups.items()}:
        item_names = set(item_counts)
        in_names = set(incoming_groups)
        missing = sorted(item_names - in_names)
        extra = sorted(in_names - item_names)
        count_diff = sorted(
            n for n in (item_names & in_names)
            if item_counts[n] != len(incoming_groups[n])
        )
        parts = []
        if missing:
            parts.append("누락: " + ", ".join(missing))
        if extra:
            parts.append("레시피에 없음: " + ", ".join(extra))
        if count_diff:
            parts.append("행 수 불일치: " + ", ".join(count_diff))
        raise RecipeMismatchError(
            "자재 구성이 레시피와 다릅니다 — 화면을 새로고침하세요. (" + " / ".join(parts) + ")"
        )

    # 그룹 내 소비 인덱스 — recipe["items"] 순서대로 같은 이름의 k번째 detail 을 꺼낸다.
    consumed: dict[str, int] = defaultdict(int)

    def take_incoming(name: str) -> dict[str, Any]:
        idx = consumed[name]
        consumed[name] += 1
        return incoming_groups[name][idx]

    # 기준 자재가 있으면 총량을 실측에서 되돌려 계산 (없으면 작업자가 고른 배치 총량 사용)
    total = float(total_amount)
    anchor = next((it for it in recipe["items"] if it.get("is_anchor")), None)
    anchor_actual: float | None = None
    anchor_weight: float | None = None
    if anchor is not None:
        anchor_actual = _opt_num(incoming_groups[str(anchor["material_name"])][0].get("actual_amount"))
        if anchor_actual is None or anchor_actual <= 0:
            raise RecipeMismatchError(
                f"기준 자재({anchor['material_name']})를 먼저 계량하세요."
            )
        # 이론량은 화면(blend_lib.computeAnchorTheory)과 **같은 산술**로 낸다 —
        # 레시피 원값(value_weight) 비례: theory_i = round(실측 × w_i / w_기준, 2).
        #
        # 옛 경로는 4자리로 반올림된 ratio(%) 를 두 번 통과시켰다(실측 → 총량 되돌리기 →
        # 다시 비율 배분). ratio 의 반올림 오차(최대 0.00005%p)가 1/ratio_기준 배로
        # 증폭돼, 기준 자재 비율이 작을수록 커진다 — 실측 0.6567%(131.33g/20kg) 기준
        # 자재에서 주자재 이론량이 화면보다 0.93g 낮게 나왔다(허용 편차 0.05g 의 18배).
        # 그 결과 작업자가 화면 목표대로 정확히 계량해도 저장이 400 으로 막히고,
        # 저장되더라도 DHR 에 작업자가 본 값과 다른 이론량이 남았다(규제 기록 무결성).
        anchor_weight = _opt_num(anchor.get("value_weight"))
        if anchor_weight is None or anchor_weight <= 0:
            # 폴백: 레시피 원값이 없는(0/NULL/컬럼 부재) 옛 데이터에서만 기존 ratio 경로.
            # 현행 get_recipe_for_blend 는 value_weight<=0 인 자재를 기준으로 인정하지
            # 않으므로(effective_anchor 무효화) 실질적으로는 도달하지 않는 방어선이다.
            anchor_weight = None
            ratio = float(anchor["ratio"] or 0)
            if ratio <= 0:
                raise RecipeMismatchError("기준 자재의 레시피 비율이 0 입니다.")
            # 저울 해상도(2자리) — 기준 자재 실측에서 파생하는 배치 총량도 2자리로 맞춘다
            # (자재 이론량은 2자리인데 총량만 3자리로 남는 불일치 방지).
            total = round(anchor_actual * 100.0 / ratio, 2)

    derived: list[dict[str, Any]] = []
    for order, item in enumerate(recipe["items"], start=1):
        name = str(item["material_name"])
        sent = take_incoming(name)
        ratio = float(item["ratio"] or 0)
        if anchor is not None:
            # 저울 해상도(2자리) — 기준 자재 파생 이론량도 2자리로 맞춘다.
            if item.get("is_anchor"):
                theory = anchor_actual          # 기준 행: 이론 = 실측 (편차 0)
            elif anchor_weight is not None:
                theory = round(anchor_actual * float(_opt_num(item.get("value_weight")) or 0.0)
                               / anchor_weight, 2)
            else:
                theory = round(total * ratio / 100.0, 2)   # ratio 폴백(옛 데이터)
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
    if anchor is not None and anchor_weight is not None:
        # 도출 총량 = 모든 행 이론량의 합(화면 computeAnchorTheory 와 동일).
        # 총량을 먼저 되돌려 계산한 뒤 다시 배분하면(옛 경로) 행 합계와 총량이 어긋난다.
        total = round(sum(float(d["theory_amount"] or 0) for d in derived), 2)
    return derived, total


def missing_actual_names(details: list[dict[str, Any]]) -> list[str]:
    """실제량(actual_amount)이 비어 있는 행의 자재명 목록(순서 보존).

    배합은 레시피의 **모든** 자재를 계량해야 성립한다. 실제량이 NULL 로 저장되면
    ① 자재 사용량 집계의 SUM 이 NULL 을 무시해 그 자재가 '투입되지 않은 것'처럼 잡히고
    ② DHR 에 실제량이 빈 줄로 남는다(규제 기록 결손). 편차 검사는 이 결손을 못 잡는다 —
    weighing_tolerance_violations 는 actual is None 이면 건너뛰고, 화면의 rowVariance 도
    빈 실제량의 편차를 0 으로 돌려주기 때문이다(그래서 화면·서버 양쪽을 다 통과했다).

    호출 시점은 enforce_carry_over · derive_details_from_recipe **이후**여야 한다 —
    반응기 이월(carried_over) 행은 enforce_carry_over 가 1차 배합 총량으로 실제량을
    강제 채우므로, 그 시점에는 정당하게 값이 있다(예외 분기 불필요).

    0 은 '계량됨(0g)' 으로 본다 — 비어 있음(None/"")만 미계량으로 판정한다.
    """
    missing: list[str] = []
    for d in details:
        if _opt_num(d.get("actual_amount")) is None:
            missing.append(str(d.get("material_name") or "").strip() or "(이름 없음)")
    return missing


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
    is_bulk_regenerated: bool = False,
) -> int:
    """배합 실적 1건 저장 (헤더 + 상세). product_lot 자동 생성.

    reactor 지정 시 실적을 진행한 반응기(1~4)를 기록한다(반응기 진행 반제품).
    manual_entry=True 면 저울 연동 중 수동 입력으로 계량됐음을 기록한다(추적성).
    is_bulk_regenerated=True 면 일괄 재생성 경로로 만든 문서·계획용 기록임을 표식한다.
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
                     worker_sign, reactor, manual_entry, is_bulk_regenerated,
                     created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_lot, recipe_id, product_name.strip(), ink_name, position, worker.strip(),
                    work_date, work_time, float(total_amount), scale,
                    (note or "").strip() or None, worker_sign,
                    int(reactor) if reactor is not None else None,
                    1 if manual_entry else 0,
                    1 if is_bulk_regenerated else 0,
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
            is_bulk_regenerated=True,
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
               manual_entry, is_bulk_regenerated,
               manual_absence_reason, manual_unacked,
               reviewed_by, reviewed_at, approved_by, approved_at,
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
    # 증량(rescale) 이력 — 공식 DHR·화면이 함께 쓰도록 기록 dict 에 실어 준다(GAP-5).
    # rescale_* 컬럼이 없는 구버전/단위테스트 스키마는 방어적으로 기본값 폴백(다른 컬럼과 동일 패턴).
    try:
        rr = connection.execute(
            "SELECT rescale_events_json, rescale_count, rescale_unacked "
            "FROM blend_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        record["rescale_events_json"] = rr["rescale_events_json"] if rr else None
        record["rescale_count"] = int(rr["rescale_count"] or 0) if rr else 0
        record["rescale_unacked"] = int(rr["rescale_unacked"] or 0) if rr else 0
    except sqlite3.OperationalError:  # rescale_* 컬럼이 없는 구버전/테스트 DB
        record["rescale_events_json"] = None
        record["rescale_count"] = 0
        record["rescale_unacked"] = 0
    # 계량 중 자재 폐기 이력 — 상세 화면이 "폐기: 자재 X g" 줄을 그릴 수 있게 실어 준다.
    try:
        dr = connection.execute(
            "SELECT discard_events_json FROM blend_records WHERE id = ?",
            (record_id,),
        ).fetchone()
        record["discard_events_json"] = dr["discard_events_json"] if dr else None
    except sqlite3.OperationalError:  # 컬럼이 없는 구버전/테스트 DB
        record["discard_events_json"] = None
    return record


def list_blend_records(
    connection: sqlite3.Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    worker: str | None = None,
    product: str | None = None,
    search: str | None = None,
    limit: int = 200,
    include_canceled: bool = False,
) -> list[dict[str, Any]]:
    # 기본은 취소분 제외(현장 목록). include_canceled=True 면 함께 조회한다 — 취소한 기록을
    # 다시 열어 '복원'하거나 취소 이력을 확인할 유일한 경로이고, 전체 Excel 백업에서도
    # 취소는 지워진 게 아니라 보존해야 할 증거라 포함이 맞다.
    clauses = [] if include_canceled else ["status != 'canceled'"]
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
    if product:
        # 제품별 필터 — 정확 일치(검색용 부분일치와 별개). 특정 제품의 여러 배치를 한 화면에서.
        clauses.append("product_name = ?")
        params.append(product)
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
    where = " AND ".join(clauses) if clauses else "1=1"
    params.append(int(limit))
    rows = connection.execute(
        f"""
        SELECT id, product_lot, recipe_id, product_name, ink_name, position, worker,
               work_date, work_time, total_amount, scale, status, note, created_at,
               manual_entry, is_bulk_regenerated
        FROM blend_records
        WHERE {where}
        ORDER BY work_date DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_serialize_record(r) for r in rows]


def count_blend_records(
    connection: sqlite3.Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    worker: str | None = None,
    product: str | None = None,
    search: str | None = None,
    include_canceled: bool = False,
) -> int:
    """list_blend_records 와 동일 필터의 전체 건수(표시 상한과 무관한 '전체 M').

    /status 기록 목록이 표시 상한(LIMIT)에 도달했는지 판정하고 '표시 N / 전체 M' 안내를
    정확히 보여주기 위한 경량 COUNT. WHERE 절은 list_blend_records 와 일치해야 한다.
    """
    clauses = [] if include_canceled else ["status != 'canceled'"]
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
    if product:
        # 제품별 필터 — 정확 일치. list_blend_records 와 조건 일치.
        clauses.append("product_name = ?")
        params.append(product)
    if search:
        clauses.append(
            "(product_lot LIKE ? OR product_name LIKE ? OR ink_name LIKE ? "
            "OR EXISTS (SELECT 1 FROM blend_details d "
            "WHERE d.blend_record_id = blend_records.id "
            "AND d.material_lot LIKE ?))"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])
    where = " AND ".join(clauses) if clauses else "1=1"
    row = connection.execute(
        f"SELECT COUNT(*) FROM blend_records WHERE {where}", params
    ).fetchone()
    return int(row[0])


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
        "is_bulk_regenerated": bool(row["is_bulk_regenerated"]) if "is_bulk_regenerated" in keys else False,
    }
    for f in ("reviewed_by", "reviewed_at", "approved_by", "approved_at",
              "worker_sign", "reviewed_sign", "approved_sign", "reactor",
              # 수기 입력(책임자 부재) 사유·미확인 플래그 — SELECT 에는 있었지만 여기서
              # 빠뜨려 상세 응답에 실리지 않았다(화면이 '사유: -' 로 표시되던 원인).
              "manual_absence_reason", "manual_unacked",
              # 취소 시각·자동 삭제 예정일 계산(F15) — 취소가 마지막 쓰기라 updated_at 이 기준.
              "updated_at"):
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


# ── 저장 멱등성(중복 저장 방지) ────────────────────────────────
# 저장 중 네트워크가 끊기면 화면은 "저장 실패"를 띄우지만 서버는 이미 커밋했을 수 있다.
# 작업자가 다시 저장하면 같은 계량값이 두 LOT 으로 남고(동일 작업자·시각·서명·자재 LOT),
# 자재 사용량 집계가 2배가 되며 둘 중 무엇이 실물인지 판별할 근거가 없다.
# generate_product_lot 이 매번 새 순번을 주므로 UNIQUE(product_lot) 도 이를 막지 못한다.
# → 클라이언트가 만든 1회용 request_id 를 저장과 **같은 트랜잭션**에 함께 기록한다.
_SAVE_REQUEST_RETENTION_DAYS = 7   # 이 기간이 지난 멱등 기록은 정리(테이블 무한 증가 방지)


def lookup_save_request(
    connection: sqlite3.Connection, request_id: str, endpoint: str
) -> list[int] | None:
    """이미 처리된 request_id 면 그때 만든 기록 id 목록을 반환(없으면 None).

    endpoint 가 다르면 None — 단건/다중 계량이 같은 id 를 우연히 공유해도 서로의
    결과를 되돌려주지 않는다(호출부가 409 로 되돌린다).
    """
    rid = (request_id or "").strip()
    if not rid:
        return None
    try:
        row = connection.execute(
            "SELECT endpoint, record_ids FROM blend_save_requests WHERE request_id = ?",
            (rid,),
        ).fetchone()
    except sqlite3.OperationalError:  # 테이블이 없는 구버전/단위테스트 스키마
        return None
    if row is None or row["endpoint"] != endpoint:
        return None
    try:
        ids = json.loads(row["record_ids"])
    except (TypeError, ValueError):
        return None
    return [int(i) for i in ids] if isinstance(ids, list) else None


def remember_save_request(
    connection: sqlite3.Connection, request_id: str, endpoint: str, record_ids: list[int]
) -> None:
    """이 request_id 로 만든 기록 id 를 남긴다(호출부의 최종 commit 에 함께 실린다).

    같은 id 가 이미 있으면 sqlite3.IntegrityError 를 그대로 올린다 — 호출부가
    롤백하고 먼저 커밋된 결과를 되돌려주게 하기 위함이다(동시 요청 경합 처리).
    저장이 400 등으로 중단되면 커밋되지 않으므로 id 는 소모되지 않는다.
    """
    from ..db.time_utils import utc_now_text

    rid = (request_id or "").strip()
    if not rid:
        return
    now = utc_now_text()
    connection.execute(
        "INSERT INTO blend_save_requests (request_id, endpoint, record_ids, created_at) "
        "VALUES (?, ?, ?, ?)",
        (rid, endpoint, json.dumps([int(i) for i in record_ids]), now),
    )
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_SAVE_REQUEST_RETENTION_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute("DELETE FROM blend_save_requests WHERE created_at < ?", (cutoff,))


# ── 증량(rescale) 승인 — 책임자 인증 토큰 발급·소비 ─────────────
_RESCALE_APPROVAL_TTL_MINUTES = 30  # 승인 유효 시간(분) — 30분 내 저장에만 쓸 수 있다.


RESCALE_PURPOSE = "rescale"     # 초과 계량 증량 승인 — 저장 시 approval_id 로 소비된다.
MANUAL_PURPOSE = "manual"       # 저울 전용 모드의 수기 입력 허용 승인 — 발급이 곧 승인.


def normalize_approval_purpose(purpose: Any) -> str:
    """승인 목적을 알려진 두 값으로 좁힌다(클라이언트 문자열 신뢰 금지).

    'manual' 외의 모든 값은 기존 동작 그대로 증량('rescale')으로 본다 — 옛 라우터가
    `purpose == "manual"` 여부만 보고 나머지를 증량으로 취급했던 것과 동일하다.
    """
    return MANUAL_PURPOSE if str(purpose or "").strip() == MANUAL_PURPOSE else RESCALE_PURPOSE


def create_rescale_approval(
    connection: sqlite3.Connection, approver: str, purpose: str = RESCALE_PURPOSE
) -> dict[str, Any]:
    """책임자 인증 성공 시 blend_rescale_approvals 행을 INSERT 하고 {approval_id, approver} 반환.

    purpose 로 '무엇을 승인했는가'를 함께 남긴다. 목적이 없던 시절에는 수기입력 승인
    토큰이 그대로 증량 승인으로 소비될 수 있었고(같은 발급 함수·같은 검사), DB 만으로는
    책임자가 무엇을 승인했는지 구분할 수 없었다.

    - purpose='rescale': 저장 시 validate_rescale_events 가 used=1 로 소비한다.
    - purpose='manual' : 소비 지점이 없다(화면이 approval_id 를 되돌려 보내지 않고
      통제는 '책임자 승인 + 기록의 manual_entry 표시'로 이루어진다). 발급 자체가
      승인 행위이므로 발급 시점에 used=1 로 닫는다 — used=0 이 '아직 안 쓴 증량 토큰'
      만을 뜻하게 되고, 쓰이지 않는 토큰이 무한히 쌓이지 않는다.
    """
    from ..db.time_utils import utc_now_text

    purpose = normalize_approval_purpose(purpose)
    used = 1 if purpose == MANUAL_PURPOSE else 0
    try:
        cursor = connection.execute(
            "INSERT INTO blend_rescale_approvals (approver, created_at, used, purpose) "
            "VALUES (?, ?, ?, ?)",
            (approver, utc_now_text(), used, purpose),
        )
    except sqlite3.OperationalError:  # purpose 컬럼이 없는 구버전/단위테스트 스키마
        cursor = connection.execute(
            "INSERT INTO blend_rescale_approvals (approver, created_at, used) VALUES (?, ?, ?)",
            (approver, utc_now_text(), used),
        )
    return {"approval_id": cursor.lastrowid, "approver": approver, "purpose": purpose}


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


_MAX_RESCALE_DRIVERS = 20  # 한 이벤트의 원인 자재 수 상한(레시피 자재 수를 넘을 이유가 없다)


def _sanitize_rescale_drivers(raw: Any) -> list[dict[str, Any]]:
    """증량 원인 자재 목록을 저장 가능한 형태로 좁힌다(클라이언트 원문 신뢰 금지).

    자재명은 문자열로 잘라 담고 수치 3종은 float 로만 받는다. 형태가 어긋난 항목은
    통째로 버리되 저장 자체는 실패시키지 않는다 — 증량은 유효한데 표시용 부가정보
    때문에 배합 기록이 막히면 현장이 멈춘다.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_RESCALE_DRIVERS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("material_name") or "").strip()[:200]
        if not name:
            continue
        entry: dict[str, Any] = {"material_name": name}
        for key in ("theory_before", "actual", "over"):
            try:
                value = float(item.get(key))
            except (TypeError, ValueError):
                continue
            if value != value or value in (float("inf"), float("-inf")):  # NaN/inf 제외
                continue
            entry[key] = round(value, 2)
        out.append(entry)
    return out


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
        # 증량을 몰아온 자재 — "어느 자재가 얼마나 넘쳐 총량이 늘었는가".
        # 이 정규화가 화이트리스트라 여기서 옮기지 않으면, 프론트가 보내고 화면이
        # 읽으려 해도 저장 단계에서 조용히 사라진다(2026-08-04 검토에서 적발).
        drivers = _sanitize_rescale_drivers(ev.get("drivers"))
        if drivers:
            norm_ev["drivers"] = drivers
        if approval_id is not None:
            # 승인 행 조회 — 목적이 증량이고, used=0 이고, 30분 이내여야 한다.
            row = connection.execute(
                "SELECT * FROM blend_rescale_approvals WHERE id = ?",
                (int(approval_id),),
            ).fetchone()
            if not row or row["used"]:
                raise RescaleApprovalError(
                    "증량 승인이 유효하지 않습니다 — 다시 인증하세요."
                )
            # 목적 검사 — 수기입력 승인(manual)을 증량으로 돌려쓰는 것을 막는다.
            # purpose 컬럼이 없는 구스키마·마이그레이션 이전 행(NULL)은 증량으로 본다
            # (하위호환 — 옛 코드에서 실제로 소비되던 유일한 목적이 증량이었다).
            row_purpose = row["purpose"] if "purpose" in row.keys() else None
            if (row_purpose or RESCALE_PURPOSE) != RESCALE_PURPOSE:
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


# 계량 중 자재 폐기 기록 상한(모델 Field 와 동일) — 서비스 단독 호출에서도 방어.
DISCARD_EVENTS_MAX = 20


def apply_discard_events_to_record(
    connection: sqlite3.Connection,
    record_id: int,
    events: list[dict[str, Any]] | None,
) -> str | None:
    """계량 중 자재 폐기 목록을 정규화해 blend_records.discard_events_json 에 기록한다.

    '처음부터 다시' 재계량에서 비커에 담긴 자재를 실제로 버린 경우의 흔적 — 편차 강제
    체계라 최종 기록은 항상 이론량과 일치하므로, 여기 남기지 않으면 버린 자재는 자재
    사용량에도 DHR 에도 나타나지 않는다. 저장을 막지 않는 순수 기록(경고·검증 없음).
    반환: 저장한 JSON 텍스트(없으면 None — 컬럼 미기록).
    """
    if not events:
        return None
    cleaned: list[dict[str, Any]] = []
    for ev in events[:DISCARD_EVENTS_MAX]:
        name = str(ev.get("material_name") or "").strip()[:200]
        amount = ev.get("amount_g")
        try:
            amount_f = round(float(amount), 2)
        except (TypeError, ValueError):
            continue
        if not name or amount_f <= 0:
            continue
        cleaned.append({
            "material_name": name,
            "material_code": str(ev.get("material_code") or "").strip()[:50],
            "amount_g": amount_f,
        })
    if not cleaned:
        return None
    events_json = json.dumps(cleaned, ensure_ascii=False)
    connection.execute(
        "UPDATE blend_records SET discard_events_json = ? WHERE id = ?",
        (events_json, record_id),
    )
    return events_json


def create_batch_discard(
    connection: sqlite3.Connection,
    *,
    recipe_id: int | None,
    product_name: str,
    worker: str,
    work_date: str,
    total_amount: float | None,
    reason: str,
    source: str,
    details: list[dict[str, Any]],
    created_by: str | None,
    created_at: str,
) -> int:
    """배치 전체 폐기 1건 기록 — blend_records 와 분리된 별도 스트림.

    제품 LOT 을 소비하지 않고, 기존 목록·집계·DHR·내보내기의 status 필터를 전혀
    건드리지 않는다(폐기는 항상 별도 스트림 — ERP 이관 시 이중 차감 방지).
    details 는 폐기 시점까지 계량돼 있던 자재 행들(무엇이 얼마나 버려졌는가).
    """
    cleaned = [{
        "material_name": str(d.get("material_name") or "").strip()[:200],
        "material_code": str(d.get("material_code") or "").strip()[:50],
        "material_lot": str(d.get("material_lot") or "").strip()[:100],
        "actual_amount": round(float(d.get("actual_amount") or 0), 2),
    } for d in details if str(d.get("material_name") or "").strip()]
    cur = connection.execute(
        """
        INSERT INTO blend_batch_discards
            (recipe_id, product_name, worker, work_date, total_amount,
             reason, source, details_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recipe_id, product_name.strip(), worker.strip(), work_date,
            round(float(total_amount), 2) if total_amount else None,
            reason.strip()[:500], source,
            json.dumps(cleaned, ensure_ascii=False), created_by, created_at,
        ),
    )
    return int(cur.lastrowid)


def list_batch_discards(
    connection: sqlite3.Connection,
    *,
    from_date: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """배치 폐기 목록(작업일 역순) — 책임자 사후 점검 화면(LOT 대사)용.

    from_date 는 호출부(라우트)가 로컬 오늘 기준으로 계산해 넘긴다(이 모듈은
    local_today_text 를 모른다 — 총량 이상 조회와 같은 분담).
    """
    rows = connection.execute(
        """
        SELECT id, recipe_id, product_name, worker, work_date, total_amount,
               reason, source, details_json, created_at
        FROM blend_batch_discards
        WHERE work_date >= ?
        ORDER BY work_date DESC, id DESC
        LIMIT ?
        """,
        (from_date, int(limit)),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        try:
            details = json.loads(r["details_json"] or "[]")
            if not isinstance(details, list):
                details = []
        except (TypeError, ValueError):
            details = []
        discarded_g = round(sum(float(d.get("actual_amount") or 0) for d in details), 2)
        items.append({
            "id": int(r["id"]),
            "recipe_id": r["recipe_id"],
            "product_name": r["product_name"],
            "worker": r["worker"],
            "work_date": r["work_date"],
            "total_amount": r["total_amount"],
            "reason": r["reason"],
            "source": r["source"],
            "details": details,
            "discarded_g": discarded_g,
            "created_at": r["created_at"],
        })
    return items


# 수기 입력 부재 사유 길이 상한(모델 Field 와 동일) — 서비스 단독 호출에서도 방어.
MANUAL_ABSENCE_REASON_MAX = 300


def apply_manual_absence_to_record(
    connection: sqlite3.Connection,
    record_id: int,
    reason: str | None,
) -> bool:
    """수기 입력 '책임자 부재 진행' 사유를 기록에 남기고 미확인(ack 대기)으로 표시한다.

    저울 전용 모드에서 비밀번호 승인 없이 사유만으로 손입력을 진행한 경우에 호출된다.
    증량 부재(rescale_unacked)와 동일하게 책임자 확인 전까지 대시보드·트레이 알림에
    남는다. 사유가 비어 있으면 아무것도 하지 않는다(일반 저장 경로 무영향).

    반환: 미확인으로 표시했으면 True.
    """
    text = (reason or "").strip()
    if not text:
        return False
    # manual_entry 도 함께 강제한다 — 부재 진행으로 손계량했다는 사실 자체가 수동 입력이다.
    # 클라이언트는 저울이 꺼져 있으면 행 단위 manual 플래그를 세우지 않을 수 있어(저울 연결
    # 중에만 표시하던 규칙), 서버가 기록 단위 표시를 보장한다.
    connection.execute(
        "UPDATE blend_records SET manual_absence_reason = ?, manual_unacked = 1, "
        "manual_entry = 1 WHERE id = ?",
        (text[:MANUAL_ABSENCE_REASON_MAX], record_id),
    )
    return True
