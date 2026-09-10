"""AI 도우미 라우트 — 스트리밍 답변, 대화 초기화, 설정.

⚠ 이 파일에 ``from __future__ import annotations`` 를 넣지 않는다.
   ``@limiter.limit`` 이 붙은 라우트가 본문 모델(Pydantic)을 받으면 지연 평가된
   주석 때문에 slowapi 가 시그니처를 잘못 읽어 422 가 난다(근태 로그인 사고
   2026-07-01, ``attendance_routes.py`` 상단 주석 참조).

공개 범위:
- ``POST /api/assistant/stream``  : 무로그인(사내망). IP 당 6회/분.
- ``POST /api/assistant/reset``   : 무로그인. 대화 이력만 지운다.
- ``GET  /api/assistant/status``  : 무로그인. 켜짐 여부와 공급자 이름만.
- ``GET/PUT /api/assistant/settings`` : 책임자 전용. 키는 가린 형태로만 나간다.
"""

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded

from ..auth import require_access_level
from ..db import get_connection, write_audit_log
from ..limiter import limiter
from ..services.assistant import session as assistant_session
from ..services.assistant import settings as assistant_settings
from ..services.assistant import stream as assistant_stream

STREAM_RATE_LIMIT = "6/minute"

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class AssistantContext(BaseModel):
    """지금 보고 있는 화면. 힌트로만 쓰인다."""

    path: Optional[str] = Field(default=None, max_length=200)
    product: Optional[str] = Field(default=None, max_length=100)
    recipe: Optional[str] = Field(default=None, max_length=100)


class AssistantQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    session_id: Optional[str] = Field(default=None, max_length=100)
    context: Optional[AssistantContext] = None


class AssistantReset(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=100)


class AssistantSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    model: Optional[str] = Field(default=None, max_length=100)
    gemini_api_key: Optional[str] = Field(default=None, max_length=400)
    groq_api_key: Optional[str] = Field(default=None, max_length=400)


@limiter.limit(STREAM_RATE_LIMIT)
def _rate_gate(request: Request) -> None:
    """스트림 시작 **전에** 한도를 본다.

    제너레이터 안에서 막으면 이미 200 이 나간 뒤라 화면이 오류를 오해한다.
    그래서 별도 함수로 떼어 라우트 첫 줄에서 부르고, 초과는 429 JSON 으로 끊는다.
    """
    return None


def build_router() -> APIRouter:
    router = APIRouter()

    # ── 스트리밍 답변 ────────────────────────────────────────────
    @router.post("/assistant/stream", response_model=None)
    def assistant_stream_route(request: Request, body: AssistantQuery) -> Any:
        try:
            _rate_gate(request=request)
        except RateLimitExceeded:
            return JSONResponse(
                status_code=429,
                content={
                    "code": assistant_stream.ERR_RATE_LIMITED,
                    # 창이 1분이라 60초 뒤면 반드시 풀린다 — 채팅방이 그렇게 말하게 한다.
                    "message": assistant_stream.rate_limited_message(60, "minute"),
                    "retry_after": 60,
                },
                headers={"Retry-After": "60"},
            )

        query = (body.query or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="BAD_REQUEST")

        session_id = (body.session_id or "").strip() or uuid.uuid4().hex
        context = body.context.model_dump() if body.context else None

        generator = assistant_stream.stream_answer(
            query, session_id=session_id, context=context
        )
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    # ── 대화 초기화 ──────────────────────────────────────────────
    @router.post("/assistant/reset")
    def assistant_reset(body: AssistantReset) -> dict[str, Any]:
        """세션 하나의 대화 이력을 지운다. 없던 세션이어도 성공으로 답한다."""
        cleared = assistant_session.clear((body.session_id or "").strip() or None)
        return {"status": "ok", "cleared": cleared}

    # ── 공개 상태 ────────────────────────────────────────────────
    @router.get("/assistant/status")
    def assistant_status() -> dict[str, Any]:
        """켜짐 여부·공급자·모델. 키는 어떤 형태로도 나가지 않는다."""
        with get_connection() as connection:
            return assistant_settings.status(connection)

    # ── 설정 조회(책임자) ────────────────────────────────────────
    @router.get("/assistant/settings")
    def get_assistant_settings(
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        with get_connection() as connection:
            return assistant_settings.admin_view(connection)

    # ── 설정 저장(책임자) ────────────────────────────────────────
    @router.put("/assistant/settings")
    def put_assistant_settings(
        body: AssistantSettingsUpdate,
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """켬/끔·모델·API 키 저장. 키를 빈 문자열로 보내면 지운다(환경변수로 복귀).

        감사 로그에는 **무엇을 바꿨는지만** 남긴다. 키 값은 남기지 않는다.
        """
        actor_label = (
            current_user.get("display_name") or current_user.get("username") or None
        )
        changed: dict[str, Any] = {}

        with get_connection() as connection:
            if body.enabled is not None:
                assistant_settings.set_enabled(
                    connection, body.enabled, updated_by=actor_label
                )
                changed["enabled"] = body.enabled

            if body.model is not None:
                model = body.model.strip()
                if not model:
                    raise HTTPException(status_code=400, detail="INVALID_MODEL")
                assistant_settings.set_model(connection, model, updated_by=actor_label)
                changed["model"] = model

            if body.gemini_api_key is not None:
                stored = assistant_settings.set_api_key(
                    connection,
                    assistant_settings.GEMINI_KEY,
                    body.gemini_api_key,
                    updated_by=actor_label,
                )
                changed["gemini_api_key"] = "set" if stored else "cleared"

            if body.groq_api_key is not None:
                stored = assistant_settings.set_api_key(
                    connection,
                    assistant_settings.GROQ_KEY,
                    body.groq_api_key,
                    updated_by=actor_label,
                )
                changed["groq_api_key"] = "set" if stored else "cleared"

            if changed:
                write_audit_log(
                    connection,
                    action="setting_assistant_set",
                    actor=current_user,
                    target_type="app_setting",
                    target_label="assistant",
                    details=changed,
                )
            connection.commit()
            view = assistant_settings.admin_view(connection)

        view["status"] = "ok"
        view["changed"] = sorted(changed)
        return view

    return router
