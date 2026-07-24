"""앱 설정 엔드포인트 — 저울 전용 입력 모드(scale-only-input) 토글.

GET 은 인증 불요(배합 화면이 무로그인이므로 공개). PUT 은 책임자 전용이며
감사 로그를 남긴다. 기본값은 OFF(행 부재 = false).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_access_level
from ..db import get_connection, write_audit_log
from ..limiter import limiter
from ..services import settings_service


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/settings/scale-only-input")
    def get_scale_only_input() -> dict[str, Any]:
        """저울 전용 입력 모드 현재 상태. 공개(배합 화면 무로그인).

        응답: {"enabled": false} (행 없음/'0' 모두 false).
        """
        with get_connection() as connection:
            enabled = settings_service.get_scale_only_input(connection)
        return {"enabled": enabled}

    @router.put("/settings/scale-only-input")
    def set_scale_only_input(
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """저울 전용 입력 모드 토글 — 책임자 전용.

        body: {"enabled": true|false}. 저장 후 감사 로그(action=setting_scale_only_set).
        응답: {"status":"ok","enabled":...}.
        """
        raw = body.get("enabled")
        if not isinstance(raw, bool):
            raise HTTPException(
                status_code=400,
                detail="enabled 는 true/false 여야 합니다.",
            )

        actor_label = (
            current_user.get("display_name")
            or current_user.get("username")
            or None
        )
        with get_connection() as connection:
            settings_service.set_scale_only_input(
                connection,
                raw,
                updated_by=actor_label,
            )
            write_audit_log(
                connection,
                action="setting_scale_only_set",
                actor=current_user,
                target_type="app_setting",
                target_label=settings_service.SCALE_ONLY_INPUT_KEY,
                details={"enabled": raw},
            )
            connection.commit()

        return {"status": "ok", "enabled": raw}

    # ── 배합 창 단일화 가드 비상 예외 코드 ──────────────────────────
    # GET/PUT 은 책임자 전용(사용자 관리 화면에서 조회·변경). verify 는 무로그인(배합 화면)
    # 이며 코드를 반환하지 않고 일치 여부만 준다 — brute-force 방지로 rate limit.
    @router.get("/settings/blend-window-override")
    def get_blend_window_override(
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """현재 예외 코드 — 책임자 전용(관리 화면 표시용)."""
        with get_connection() as connection:
            code = settings_service.get_blend_window_override_code(connection)
        return {"code": code}

    @router.put("/settings/blend-window-override")
    def set_blend_window_override(
        body: dict[str, Any],
        current_user: dict[str, Any] = Depends(require_access_level("manager")),
    ) -> dict[str, Any]:
        """예외 코드 변경 — 책임자 전용. 감사 로그(코드 값은 남기지 않음)."""
        code = str(body.get("code") or "").strip()
        if len(code) < 4 or len(code) > 32:
            raise HTTPException(
                status_code=400,
                detail="코드는 4자 이상 32자 이하여야 합니다.",
            )
        actor_label = (
            current_user.get("display_name")
            or current_user.get("username")
            or None
        )
        with get_connection() as connection:
            settings_service.set_blend_window_override_code(
                connection, code, updated_by=actor_label
            )
            write_audit_log(
                connection,
                action="setting_blend_window_override_set",
                actor=current_user,
                target_type="app_setting",
                target_label=settings_service.BLEND_WINDOW_OVERRIDE_KEY,
                details={"length": len(code)},  # 코드 값 자체는 남기지 않는다.
            )
            connection.commit()
        return {"status": "ok"}

    @router.post("/settings/blend-window-override/verify")
    @limiter.limit("10/minute")
    def verify_blend_window_override(
        request: Request,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """입력 코드 대조 — 무로그인(배합 화면 비상구). 코드는 반환하지 않고 ok 만."""
        code = str(body.get("code") or "").strip()
        with get_connection() as connection:
            ok = settings_service.verify_blend_window_override_code(connection, code)
        return {"ok": ok}

    return router
