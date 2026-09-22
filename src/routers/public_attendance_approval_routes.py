"""근태허가원 수집 수신 — 내부망 공개 라우터(docs/attendance-approvals.md §3).

포털 로그인·검색은 이미 있는 수집기(`C:\\X\\Server_API\\webcloring-pdf`)가 하고,
BRM 은 결과만 받는다. BRM 에 브라우저 자동화·포털 자격증명을 두지 않는다는 원칙
(§1.2)이 이 라우터의 존재 이유다.

    POST /api/public/attendance-approvals
      {source, collected_at, items: [{doc_no, emp_name, emp_id, kind, half,
                                      start_date, end_date, status, drafted_at,
                                      title_raw, doc_hash}, ...]}
      → {received, created, updated, unchanged, rejected: [{doc_no, reason}]}

경계는 다른 공개 라우터와 같다(main.py protected_prefixes): 개발/내부망은 사설 IP
허용, 운영은 `X-IRMS-Tray-Token` 헤더 필수. 쓰기지만 CSRF 면제다 — 호출자가 브라우저가
아니라 사내망 배치 스크립트이고, 경계는 위 두 겹이 담당한다(main.py exempt_urls).

응답에는 종류·사유 같은 개인 사정을 싣지 않는다(§1.3). 거절 사유도 고정 문구만
돌려준다 — 그 문구는 attendance_approvals.validate_item 이 소유한다.
"""

# NOTE: 이 파일에는 `from __future__ import annotations` 를 넣지 말 것.
# 본문 모델(ApprovalBatchRequest)을 쓰는 POST 라우터라, 나중에 이 파일에
# @limiter.limit 를 얹으면 타입힌트가 문자열로 남아 본문이 쿼리로 오인되고
# 422 로 거부된다(attendance_routes.py 상단 주석의 사고와 같은 함정).
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import get_db, write_audit_log
from ..services import attendance_approvals


class ApprovalBatchRequest(BaseModel):
    source: str = Field(default="portal", max_length=40)
    collected_at: str = Field(default="", max_length=40)
    # 항목은 느슨하게 받는다. 한 건의 형식 오류가 배치 전체를 422 로 만들면
    # "한 건이 나빠도 나머지는 적재한다"(§3)는 계약이 깨진다 — 검사는 건별로 한다.
    items: list[Any] = Field(default_factory=list)


def build_router() -> APIRouter:
    router = APIRouter(
        prefix="/public/attendance-approvals", tags=["public-attendance-approvals"]
    )

    @router.post("")
    def receive(
        body: ApprovalBatchRequest,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        if len(body.items) > attendance_approvals.MAX_BATCH_ITEMS:
            raise HTTPException(status_code=422, detail="TOO_MANY_ITEMS")

        result = attendance_approvals.upsert_batch(
            connection,
            items=body.items,
            source=body.source,
            collected_at=body.collected_at,
        )
        # 감사 로그는 실행마다 한 줄(건마다 남기면 200 줄이 감사 화면을 덮는다).
        write_audit_log(
            connection,
            action="attendance_approvals_collected",
            target_type="attendance_approvals",
            target_id=result["received"],
            target_label=(
                f"수집 {result['received']}건 · 신규 {result['created']} · "
                f"갱신 {result['updated']} · 그대로 {result['unchanged']} · "
                f"거절 {len(result['rejected'])}"
            ),
            details={
                "source": (body.source or "portal")[:40],
                "collected_at": body.collected_at or None,
                "received": result["received"],
                "created": result["created"],
                "updated": result["updated"],
                "unchanged": result["unchanged"],
                "rejected": len(result["rejected"]),
                # 사유만 센다 — 문서번호·이름은 감사 화면에 필요하지 않다.
                "reasons": sorted({item["reason"] for item in result["rejected"]}),
            },
        )
        connection.commit()
        return result

    return router
