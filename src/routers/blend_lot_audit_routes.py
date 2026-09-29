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


# 1회 배합 상한이 적용되지 않는 분류. 물은 유량계·부피로 재고 배치가 통째로 커서
# 25,000 g 기준이 뜻을 갖지 않는다 — 저울 전용 잠금을 용수에서 푼 것과 같은 결이다
# (사용자 결정 2026-09-29: S+코팅·베타 등 용수 레시피 전부). 실측으로 걸리던 7건이
# 모두 이 분류였다.
TOTAL_LIMIT_EXEMPT_CATEGORY = "용수"

# 총량 이상으로 볼 조건. 목록과 배지가 같은 규칙을 쓰도록 한 곳에 둔다.
#   · 25kg 을 **증량해서** 넘긴 건(폐기 권고를 무시하고 진행) — 증량 이력이 없는
#     25kg 초과는 레시피 기준 총량이 원래 큰 제품이라 이상이 아니다(2026-09-29).
#   · 증량 이력 없이 총량만 키운 우회 의심.
#   · 용수 분류 레시피는 어느 쪽으로도 세지 않는다.
_TOTAL_ANOMALY_WHERE = (
    "((COALESCE(br.oversize_total, 0) = 1 AND COALESCE(br.rescale_count, 0) > 0)"
    " OR COALESCE(br.total_bypass_suspect, 0) = 1)"
    " AND COALESCE(rc.category, '') <> :exempt_category"
)

# 목록·배지가 함께 쓰는 조인. 레시피가 없는 기록(수기 입력)은 분류가 없어 제외되지 않는다.
_TOTAL_ANOMALY_FROM = (
    "FROM blend_records br LEFT JOIN recipes rc ON rc.id = br.recipe_id"
)


def _window_start(days: int) -> str:
    """오늘에서 `days` 일 앞선 날짜(YYYY-MM-DD) — 목록·배지가 같은 창을 쓴다."""
    today = date.fromisoformat(local_today_text())
    return date.fromordinal(max(1, today.toordinal() - int(days))).isoformat()


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
        days: int = Query(default=180, ge=1, le=3650),
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        # 맞추기는 blend_service 가 한다 — 표기만 다른 같은 LOT(PB26080502 ↔ 26080502)을
        # 어긋남으로 세지 않기 위해 글자 그대로 비교하지 않는다(2026-09-29).
        # ack 를 지우거나 표시를 바꾸는 쓰기 동작은 이 화면에 없다.
        # 총량 이상과 같은 180일 창. 끝내 해소되지 않는 건이 쌓여 목록이 한없이
        # 길어지지 않게 한다 — 창 밖 건은 `older` 로 세기만 한다(2026-09-29).
        audit = blend_service.unresolved_lot_acks(connection, since=_window_start(days))
        rows = audit["items"]

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
            # 해소된 건수 — "대사가 살아서 돌고 있다"를 보여주는 참고 수치.
            "resolved": int(audit["resolved"]),
            # 창 밖(기본 180일)에서 아직 미해소인 건. 목록에는 없지만 숫자로 남긴다.
            "older": int(audit.get("older") or 0),
            "window_days": int(days),
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
            SELECT br.id, br.product_lot, br.product_name, br.work_date, br.worker,
                   br.total_amount, br.recipe_id, br.rescale_count,
                   COALESCE(br.oversize_total, 0) AS oversize_total,
                   COALESCE(br.total_bypass_suspect, 0) AS total_bypass_suspect,
                   br.total_bypass_base, br.created_at
            """ + _TOTAL_ANOMALY_FROM + """
            WHERE br.status = 'completed'
              AND br.work_date >= :from_date
              AND (""" + _TOTAL_ANOMALY_WHERE + """)
            ORDER BY br.work_date DESC, br.id DESC
            LIMIT 1000
            """,
            {"from_date": from_date, "exempt_category": TOTAL_LIMIT_EXEMPT_CATEGORY},
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

    # ------------------------------------------------------------------
    # 4. GET /blend/lot-audit/count — 메뉴 배지용 숫자(책임자 전용)
    # ------------------------------------------------------------------
    @router.get("/blend/lot-audit/count")
    def lot_audit_count(
        days: int = Query(default=180, ge=1, le=3650),
        connection: sqlite3.Connection = Depends(get_db),
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """사이드바 메뉴 옆 배지가 읽는 값 — 목록 대신 숫자만.

        이 화면은 들어와야만 보이는 자리라 아무도 열지 않았다(2026-09-29 검토).
        메뉴에 숫자가 붙으면 열어야 할 때를 메뉴가 먼저 말해 준다. 목록을 통째로
        받아 길이만 세면 매 화면마다 1,000행을 나르므로 숫자만 돌려준다.
        """
        from_date = _window_start(days)
        audit = blend_service.unresolved_lot_acks(connection, since=from_date)
        row = connection.execute(
            "SELECT COUNT(*) AS n " + _TOTAL_ANOMALY_FROM
            + " WHERE br.status = 'completed' AND br.work_date >= :from_date AND ("
            + _TOTAL_ANOMALY_WHERE + ")",
            {"from_date": from_date, "exempt_category": TOTAL_LIMIT_EXEMPT_CATEGORY},
        ).fetchone()
        unresolved = audit["items"]
        return {
            "unresolved": len(unresolved),
            "unacknowledged": sum(1 for r in unresolved if not r["acknowledged"]),
            "anomalies": int(row["n"] or 0) if row else 0,
        }

    return router
