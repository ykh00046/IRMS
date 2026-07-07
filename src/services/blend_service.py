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
    """
    current = int(recipe_id)
    seen: set[int] = {current}
    while True:
        row = connection.execute(
            "SELECT id FROM recipes WHERE revision_of = ? "
            "AND status NOT IN ('canceled', 'draft') ORDER BY id DESC LIMIT 1",
            (current,),
        ).fetchone()
        if not row or int(row["id"]) in seen:
            return current
        current = int(row["id"])
        seen.add(current)


def get_recipe_for_blend(
    connection: sqlite3.Connection, recipe_id: int, total_amount: float | None = None
) -> dict[str, Any] | None:
    """레시피와 자재 목록을 비율·이론량과 함께 반환 (배합 입력 화면용).

    total_amount 미지정 시 레시피 절대중량 합계를 기본 배치 총량으로 사용.
    개정된 레시피 id 가 오면 최신 개정판으로 자동 귀결.
    """
    recipe_id = _resolve_latest_revision(connection, recipe_id)
    recipe = connection.execute(
        "SELECT id, product_name, position, ink_name, status, base_total AS base_total_setting "
        "FROM recipes WHERE id = ?",
        (recipe_id,),
    ).fetchone()
    if not recipe:
        return None

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

    weights = [float(r["value_weight"] or 0) for r in rows]
    base_total = sum(weights)
    total = float(total_amount) if total_amount and total_amount > 0 else base_total
    ratios = compute_ratios(weights)
    theory = scale_theory(weights, total)

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
        })
    # 기준 배합량: 레시피에 저장된 값 우선(레시피 관리에서 입력), 없으면 자재 합계.
    stored_base = recipe["base_total_setting"]
    default_total = float(stored_base) if stored_base and float(stored_base) > 0 else base_total
    return {
        "recipe": {
            "id": int(recipe["id"]),
            "product_name": recipe["product_name"],
            "position": recipe["position"],
            "ink_name": recipe["ink_name"],
            "status": recipe["status"],
            "use_reactor": product_uses_reactor(connection, recipe["product_name"]),
        },
        "base_total": round(base_total, 3),
        "default_total": round(default_total, 3),
        "total_amount": round(total, 3),
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


def list_blend_recipes(connection: sqlite3.Connection, *, dhr: bool = False) -> list[dict[str, Any]]:
    """배합에 쓸 수 있는 레시피 목록 (취소/초안 제외).

    dhr=False(기본): 일반 레시피. dhr=True: DHR 전용 레시피(일괄 배합일지 생성용).
    """
    rows = connection.execute(
        """
        SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
               COUNT(ri.id) AS item_count,
               COALESCE(SUM(ri.value_weight), 0) AS total_weight
        FROM recipes r
        LEFT JOIN recipe_items ri ON ri.recipe_id = r.id
        WHERE r.status NOT IN ('canceled', 'draft')
          AND COALESCE(r.is_dhr, 0) = ?
          AND r.id NOT IN (SELECT revision_of FROM recipes WHERE revision_of IS NOT NULL)
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
WEIGHING_TOLERANCE_G = 0.05


def weighing_tolerance_violations(details: list[dict[str, Any]]) -> list[str]:
    """허용 편차(±0.05g/자재)를 넘는 자재명 목록. 실제량 미입력(None)은 검사 제외."""
    offenders: list[str] = []
    for d in details:
        theory = _opt_num(d.get("theory_amount"))
        actual = _opt_num(d.get("actual_amount"))
        if theory is None or actual is None:
            continue
        if abs(actual - theory) > WEIGHING_TOLERANCE_G + 1e-9:
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
) -> int:
    """배합 실적 1건 저장 (헤더 + 상세). product_lot 자동 생성.

    reactor 지정 시 실적을 진행한 반응기(1~4)를 기록한다(반응기 진행 반제품).
    """
    product_lot = generate_product_lot(connection, product_name, work_date)
    cur = connection.execute(
        """
        INSERT INTO blend_records
            (product_lot, recipe_id, product_name, ink_name, position, worker,
             work_date, work_time, total_amount, scale, status, note,
             worker_sign, reactor, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
        """,
        (
            product_lot, recipe_id, product_name.strip(), ink_name, position, worker.strip(),
            work_date, work_time, float(total_amount), scale,
            (note or "").strip() or None, worker_sign,
            int(reactor) if reactor is not None else None,
            created_by, created_at, created_at,
        ),
    )
    record_id = int(cur.lastrowid)

    for idx, d in enumerate(details):
        connection.execute(
            """
            INSERT INTO blend_details
                (blend_record_id, material_id, material_code, material_name,
                 material_lot, ratio, theory_amount, actual_amount, sequence_order, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                 material_lot, ratio, theory_amount, actual_amount, sequence_order, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def get_blend_record(connection: sqlite3.Connection, record_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT id, product_lot, recipe_id, product_name, ink_name, position, worker,
               work_date, work_time, total_amount, scale, status, note, reactor,
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
               ratio, theory_amount, actual_amount, sequence_order
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
        clauses.append("(product_lot LIKE ? OR product_name LIKE ? OR ink_name LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    where = " AND ".join(clauses)
    params.append(int(limit))
    rows = connection.execute(
        f"""
        SELECT id, product_lot, recipe_id, product_name, ink_name, position, worker,
               work_date, work_time, total_amount, scale, status, note, created_at
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
