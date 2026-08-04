"""미해소 LOT 대사 + 총 배합량 이상 — 책임자 전용 사후 점검 라우터 (2026-08-04).

배경(A): 앞 단계(1차) 배합 기록에 없는 반제품 LOT 로 진행하면 예전에는 저장을 막았다.
2026-08-04 에 차단을 풀고(정당한 경우에도 매번 걸려 작업자가 사유란에 아무 글자나 치고
넘어가면서 통제가 형해화됐다) 대신 진행 사실을 blend_lot_acks 에 남기게 했다. 그 대가로
**오타가 그대로 통과**하게 됐고, 이 라우터가 그것을 사후에 걸러내는 자리다.
이 화면이 없으면 통제가 사라진 상태와 같다.

대사 규칙:
    blend_lot_acks 의 (material_name, material_lot) 이 blend_records 의
    (product_name, product_lot, status='completed') 로 **나중에라도** 생겼는가.
      · 생겼으면  → 해소(1차 저장이 늦었을 뿐). 목록에서 자동으로 빠진다.
      · 안 생겼으면 → 오타이거나 실제로 없는 반제품. 이게 봐야 할 대상.
    즉 대사는 자기 치유된다 — 별도의 '해소 처리' 버튼이 필요 없다.

배경(C): 총량 플래그(blend_records.oversize_total / total_bypass_suspect)도 책임자가
볼 곳이 필요하다. 성격이 '사후 점검'으로 같아 같은 화면에 얹는다.

Endpoints (모두 책임자 전용 — require_access_level("manager")):
    GET /blend/lot-audit/unresolved       미해소 LOT 목록(경과 일수 + 미확인 구분)
    GET /blend/lot-audit/total-anomalies  총량 이상 기록(25kg 초과 / 증량 우회 의심)

blend_rescale_ack_routes.py 의 구조·권한 관례를 그대로 따른다.
`from __future__ import annotations` 사용 금지(프로젝트 제약).
"""

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..auth import require_access_level
from ..db import get_db, local_today_text
from ..services import blend_service


def _age_days(created_at: Any, today: str) -> int | None:
    """created_at(ISO8601 'YYYY-MM-DDTHH:MM:SSZ') → 오늘까지의 경과 일수.

    날짜 부분만 쓴다(저장은 UTC, '오늘'은 로컬 — 단일 사이트 KST 운영이라 하루
    경계에서 ±1 이 생길 수 있으나 '오래됐는가' 판단에는 영향이 없다).
    파싱 불가(NULL·깨진 값)면 None — 정렬·표시에서 '알 수 없음'으로 다룬다.
    """
    text = str(created_at or "").strip()
    if len(text) < 10:
        return None
    try:
        made = date.fromisoformat(text[:10])
        now = date.fromisoformat(today)
    except ValueError:
        return None
    return (now - made).days


def build_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # 1. GET /blend/lot-audit/unresolved — 미해소 LOT 대사(책임자 전용)
    # ------------------------------------------------------------------
    @router.get("/blend/lot-audit/unresolved")
    def list_unresolved_lot_acks(
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        # NOT EXISTS 가 대사의 전부다 — 1차 completed 기록이 나중에 생기면 그 순간부터
        # 이 목록에서 빠진다(해소). ack 를 지우거나 표시를 바꾸는 쓰기 동작은 없다.
        #
        # 취소된 배합의 ack 는 제외한다: br.status = 'completed'. 취소(canceled)나
        # 임시(draft) 기록에 딸린 ack 는 더 이상 통제 대상이 아니다(그 배합 자체가
        # 없던 일이 됐다).
        rows = connection.execute(
            """
            SELECT a.id AS ack_id, a.record_id, a.material_name, a.material_lot,
                   a.reason, a.acknowledged, a.created_at,
                   br.product_name, br.product_lot, br.work_date, br.worker
            FROM blend_lot_acks a
            JOIN blend_records br ON br.id = a.record_id
            WHERE br.status = 'completed'
              AND NOT EXISTS (
                  SELECT 1 FROM blend_records r2
                  WHERE r2.product_name = a.material_name
                    AND r2.product_lot = a.material_lot
                    AND r2.status = 'completed'
              )
            ORDER BY a.created_at ASC, a.id ASC
            LIMIT 1000
            """
        ).fetchall()

        # 해소된 건수 — "대사가 살아서 돌고 있다"를 화면에 보여주기 위한 참고 수치.
        # (해소된 건은 목록에 넣지 않는다. 오래된 정상 건이 화면을 덮으면 봐야 할
        #  대상이 묻힌다.)
        resolved_row = connection.execute(
            """
            SELECT COUNT(*) AS n
            FROM blend_lot_acks a
            JOIN blend_records br ON br.id = a.record_id
            WHERE br.status = 'completed'
              AND EXISTS (
                  SELECT 1 FROM blend_records r2
                  WHERE r2.product_name = a.material_name
                    AND r2.product_lot = a.material_lot
                    AND r2.status = 'completed'
              )
            """
        ).fetchone()

        today = local_today_text()
        items = []
        for r in rows:
            items.append({
                "ack_id": int(r["ack_id"]),
                "record_id": int(r["record_id"]),
                "material_name": r["material_name"],
                "material_lot": r["material_lot"],
                "reason": r["reason"] or "",
                # 0 = 작업자가 확인 창조차 못 본 경로로 저장됨(조회 실패 fail-open,
                # 초안 복구 등). 오타 여부와 별개로 '확인 절차가 없었다'는 다른 신호다.
                "acknowledged": bool(r["acknowledged"]),
                "created_at": r["created_at"],
                "age_days": _age_days(r["created_at"], today),
                "product_name": r["product_name"],
                "product_lot": r["product_lot"],
                "work_date": r["work_date"],
                "worker": r["worker"],
            })
        return {
            "items": items,
            "total": len(items),
            "unacknowledged": sum(1 for it in items if not it["acknowledged"]),
            "resolved": int(resolved_row["n"] or 0) if resolved_row else 0,
        }

    # ------------------------------------------------------------------
    # 2. GET /blend/lot-audit/total-anomalies — 총량 이상(책임자 전용)
    # ------------------------------------------------------------------
    @router.get("/blend/lot-audit/total-anomalies")
    def list_total_anomalies(
        days: int = Query(default=180, ge=1, le=3650),
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """총량 플래그가 켜진 기록. 취소된 배합은 제외(대사 목록과 같은 규칙).

        days = 조회 창(작업일 기준, 기본 180일). 플래그는 저장 시점에 한 번 계산돼
        컬럼에 남으므로 조회는 단순 필터다 — 레시피가 나중에 바뀌어도 판정이 흔들리지
        않는다(비교 기준은 total_bypass_base 에 값 자체로 보존).
        """
        today = date.fromisoformat(local_today_text())
        from_date = date.fromordinal(max(1, today.toordinal() - int(days))).isoformat()
        rows = connection.execute(
            """
            SELECT id, product_lot, product_name, work_date, worker, total_amount,
                   recipe_id, rescale_count,
                   COALESCE(oversize_total, 0) AS oversize_total,
                   COALESCE(total_bypass_suspect, 0) AS total_bypass_suspect,
                   total_bypass_base, created_at
            FROM blend_records
            WHERE status = 'completed'
              AND work_date >= ?
              AND (COALESCE(oversize_total, 0) = 1 OR COALESCE(total_bypass_suspect, 0) = 1)
            ORDER BY work_date DESC, id DESC
            LIMIT 1000
            """,
            (from_date,),
        ).fetchall()

        items = []
        for r in rows:
            total = float(r["total_amount"] or 0.0)
            base = r["total_bypass_base"]
            base_value = float(base) if base is not None else None
            items.append({
                "id": int(r["id"]),
                "product_lot": r["product_lot"],
                "product_name": r["product_name"],
                "work_date": r["work_date"],
                "worker": r["worker"],
                "total_amount": round(total, 2),
                "recipe_id": r["recipe_id"],
                "rescale_count": int(r["rescale_count"] or 0),
                "oversize_total": bool(r["oversize_total"]),
                "over_limit_g": (
                    round(total - blend_service.BLEND_OVERSIZE_FLAG_G, 2)
                    if r["oversize_total"] else None
                ),
                "total_bypass_suspect": bool(r["total_bypass_suspect"]),
                "base_total": base_value,
                "excess_pct": (
                    round((total - base_value) / base_value * 100, 1)
                    if base_value else None
                ),
            })
        return {
            "items": items,
            "total": len(items),
            "oversize": sum(1 for it in items if it["oversize_total"]),
            "bypass_suspect": sum(1 for it in items if it["total_bypass_suspect"]),
            "limit_g": blend_service.BLEND_OVERSIZE_FLAG_G,
            "max_total_g": blend_service.BLEND_TOTAL_MAX_G,
            "range": {"from": from_date, "to": today.isoformat()},
        }

    # ------------------------------------------------------------------
    # 3. GET /blend/lot-audit/batch-discards — 배치 폐기 기록(책임자 전용)
    # ------------------------------------------------------------------
    @router.get("/blend/lot-audit/batch-discards")
    def list_batch_discards(
        days: int = Query(default=180, ge=1, le=3650),
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """배치 전체 폐기 기록 — 과중량·3회 증량 차단 뒤 협의 폐기.

        성격이 '사후 점검'이라 이 화면에 얹는다(총량 이상과 동일 조회 창).
        제품 LOT 없이 별도 테이블(blend_batch_discards)에 남는 스트림이다.
        """
        today = date.fromisoformat(local_today_text())
        from_date = date.fromordinal(max(1, today.toordinal() - int(days))).isoformat()
        items = blend_service.list_batch_discards(
            connection, from_date=from_date, limit=500,
        )
        return {
            "items": items,
            "total": len(items),
            "discarded_g": round(sum(it["discarded_g"] for it in items), 2),
            "range": {"from": from_date, "to": today.isoformat()},
        }

    return router
