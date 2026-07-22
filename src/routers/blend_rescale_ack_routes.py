"""증량 승인제 — 책임자 확인(ack)·조회 라우터 (rescale-approval 2026-07-22).

'책임자 부재 진행'으로 저장된 증량(blend_records.rescale_unacked=1)을 책임자가
사후 확인하는 흐름. 대시보드 카드·트레이 알림이 이 미확인 건수를 소비한다.

계약(구현은 이 파일에 채운다):
    GET  /blend/rescales/unacked            책임자 전용 — 미확인 증량 기록 목록
                                            {items: [{id, product_name, product_lot,
                                              work_date, worker, rescale_events: [...]}], total}
    POST /blend/records/{record_id}/rescale-ack   책임자 전용 — 확인 처리(rescale_unacked=0)
                                            + audit action="blend_rescale_acked"

    (추가) GET /blend/rescales/summary       조회 개방 — rescale_count>0 인 기록의 요약
                                            [{id, rescale_count, rescale_unacked, rescale_events}]
        · 배합 기록(/status) 목록 배지·상세 모달이 소비. blend_records 목록/상세 응답은
          다른 에이전트가 편집 중(blend_service.py·blend_routes.py)이라, 공용 read
          엔드포인트를 건드리지 않고 자체 요약 엔드포인트로 병합 안전하게 노출한다.
        · /blend/records(목록) 와 동일하게 무로그인 개방(권한 의존성 없음).

blend_routes.py 와의 파일 분리는 병렬 작업 충돌 방지 목적(2026-07-22).
`from __future__ import annotations` 사용 금지(프로젝트 제약).
"""

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_access_level
from ..db import get_connection, get_db, write_audit_log


def _parse_events(raw: Any) -> list[dict[str, Any]]:
    """rescale_events_json(TEXT) 를 방어적으로 파싱해 dict 리스트로 반환.

    NULL·빈문자열·깨진 JSON·리스트 아님 → 모두 빈 리스트. 각 원소는 dict 만 통과.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [e for e in parsed if isinstance(e, dict)]


def build_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # 1. GET /blend/rescales/unacked — 미확인 증량 목록(책임자 전용)
    # ------------------------------------------------------------------
    @router.get("/blend/rescales/unacked")
    def list_unacked_rescales(
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, product_name, product_lot, work_date, worker,
                       rescale_events_json, rescale_count
                FROM blend_records
                WHERE rescale_unacked = 1 AND status != 'canceled'
                ORDER BY work_date DESC, id DESC
                """
            ).fetchall()

        items = [
            {
                "id": int(r["id"]),
                "product_name": r["product_name"],
                "product_lot": r["product_lot"],
                "work_date": r["work_date"],
                "worker": r["worker"],
                "rescale_count": int(r["rescale_count"] or 0),
                "rescale_events": _parse_events(r["rescale_events_json"]),
            }
            for r in rows
        ]
        return {"items": items, "total": len(items)}

    # ------------------------------------------------------------------
    # 2. POST /blend/records/{record_id}/rescale-ack — 확인 처리(책임자 전용)
    # ------------------------------------------------------------------
    @router.post("/blend/records/{record_id}/rescale-ack")
    def ack_rescale(
        record_id: int,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, product_lot, rescale_unacked FROM blend_records WHERE id = ?",
                (record_id,),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")

            # 이미 확인됨 → 멱등 처리(재확인은 성공으로 취급, audit 은 남기지 않음).
            if not row["rescale_unacked"]:
                return {"status": "ok", "record_id": record_id, "acked_already": True}

            connection.execute(
                "UPDATE blend_records SET rescale_unacked = 0 WHERE id = ?",
                (record_id,),
            )
            write_audit_log(
                connection,
                action="blend_rescale_acked",
                actor=current_user,
                target_type="blend_record",
                target_id=record_id,
                target_label=row["product_lot"],
                details={"product_lot": row["product_lot"]},
            )
            connection.commit()

        return {"status": "ok", "record_id": record_id, "acked_already": False}

    # ------------------------------------------------------------------
    # 3. GET /blend/rescales/summary — 증량 요약(조회 개방, 배지·모달용)
    # ------------------------------------------------------------------
    # blend_records 목록/상세(blend_service·blend_routes)는 다른 에이전트가 편집 중이라
    # 그 응답에 rescale 필드를 얹지 않고, 여기서 rescale_count>0 인 기록만 별도로 노출한다.
    # 무로그인 개방 화면(/status)이 소비하므로 권한 의존성을 두지 않는다.
    @router.get("/blend/rescales/summary")
    def rescales_summary(
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        rows = connection.execute(
            """
            SELECT id, rescale_count, rescale_unacked, rescale_events_json
            FROM blend_records
            WHERE rescale_count > 0 AND status != 'canceled'
            ORDER BY id DESC
            LIMIT 1000
            """
        ).fetchall()
        items = [
            {
                "id": int(r["id"]),
                "rescale_count": int(r["rescale_count"] or 0),
                "rescale_unacked": bool(r["rescale_unacked"]),
                "rescale_events": _parse_events(r["rescale_events_json"]),
            }
            for r in rows
        ]
        return {"items": items, "total": len(items)}

    return router
