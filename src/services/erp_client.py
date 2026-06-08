"""ERP 발주 전송 어댑터.

범용 HTTP JSON POST 방식. ``IRMS_ERP_ENDPOINT`` 가 설정되어 있으면 실제 전송,
없으면 **Mock 모드**(외부 호출 없이 성공 처리)로 동작한다. 현장 ERP API 스펙이
확정되기 전까지 안전하게 발주서 기능을 운영하기 위한 폴백이다.

Plan:   docs/01-plan/features/order-sheet-erp.plan.md §3.4
Design: docs/02-design/features/order-sheet-erp.design.md §4
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..config import ERP_API_KEY, ERP_ENDPOINT, ERP_TIMEOUT

_MAX_BODY = 1000  # 응답 본문 저장 상한(로그 비대 방지)


@dataclass
class ErpResult:
    ok: bool
    mode: str  # 'http' | 'mock'
    status_code: int
    body: str


def send_order(payload: dict[str, Any]) -> ErpResult:
    """발주서 payload 를 ERP 로 전송한다.

    엔드포인트 미설정 시 Mock 성공을 반환한다(외부 호출 0).
    설정 시 2xx 응답이면 성공, 그 외/예외면 실패로 본문 요약을 담아 반환한다.
    """
    if not ERP_ENDPOINT:
        return ErpResult(ok=True, mode="mock", status_code=200, body='{"mock": true}')

    headers = {"Content-Type": "application/json"}
    if ERP_API_KEY:
        headers["Authorization"] = f"Bearer {ERP_API_KEY}"

    try:
        response = httpx.post(
            ERP_ENDPOINT, json=payload, headers=headers, timeout=ERP_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return ErpResult(ok=False, mode="http", status_code=0, body=str(exc)[:_MAX_BODY])

    ok = 200 <= response.status_code < 300
    return ErpResult(
        ok=ok,
        mode="http",
        status_code=response.status_code,
        body=response.text[:_MAX_BODY],
    )
